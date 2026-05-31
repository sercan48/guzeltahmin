import logging
from datetime import datetime, timezone, timedelta
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy import func, distinct

from app.models.subscription import Subscription
from app.models.payment import Payment
from app.models.product import ProductPackage
from app.models.user import User
from app.models.affiliate import Affiliate, AffiliateCommission, AffiliatePayout
from app.models.referral import ReferralEvent
from app.models.coupon import CouponRedemption

logger = logging.getLogger(__name__)


class AnalyticsService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_overview_metrics(self) -> dict:
        """Calculate MRR, ARR, ARPU, LTV, Active Subscribers, Trial Users, and Conversion rates."""
        now = datetime.now(timezone.utc)

        # 1. Active Subscribers count
        active_subs_res = await self.db.execute(
            select(func.count(distinct(Subscription.user_id))).filter(
                Subscription.is_active == True, Subscription.end_date > now
            )
        )
        active_subscribers = active_subs_res.scalar() or 0

        # 2. Trial Users count
        trial_users_res = await self.db.execute(
            select(func.count(User.id)).filter(User.trial_used == True)
        )
        total_trial_users = trial_users_res.scalar() or 0

        # 3. Active Trial Subscribers count
        trial_package_res = await self.db.execute(
            select(ProductPackage.id).filter(ProductPackage.name == "Free Trial")
        )
        trial_package_id = trial_package_res.scalar()
        
        active_trials = 0
        if trial_package_id:
            active_trials_res = await self.db.execute(
                select(func.count(Subscription.id)).filter(
                    Subscription.package_id == trial_package_id,
                    Subscription.is_active == True,
                    Subscription.end_date > now,
                )
            )
            active_trials = active_trials_res.scalar() or 0

        # 4. MRR (Monthly Recurring Revenue)
        # Sum of normalized monthly value of all active non-trial subscriptions
        mrr = 0.0
        if trial_package_id:
            active_paid_subs = await self.db.execute(
                select(Subscription, ProductPackage)
                .join(ProductPackage, Subscription.package_id == ProductPackage.id)
                .filter(
                    Subscription.is_active == True,
                    Subscription.end_date > now,
                    Subscription.package_id != trial_package_id,
                )
            )
            for sub, pkg in active_paid_subs:
                duration = pkg.duration_days or 30
                daily_rate = pkg.price / duration
                mrr += daily_rate * 30.0
        else:
            active_paid_subs = await self.db.execute(
                select(Subscription, ProductPackage)
                .join(ProductPackage, Subscription.package_id == ProductPackage.id)
                .filter(Subscription.is_active == True, Subscription.end_date > now)
            )
            for sub, pkg in active_paid_subs:
                duration = pkg.duration_days or 30
                daily_rate = pkg.price / duration
                mrr += daily_rate * 30.0

        # 5. ARR (Annual Recurring Revenue)
        arr = mrr * 12.0

        # 6. Conversion Rate (Trial -> Paid)
        trial_to_paid_conversion = 0.0
        if total_trial_users > 0:
            # Find users who used trial AND have at least one paid payment afterwards
            converted_users_res = await self.db.execute(
                select(func.count(distinct(User.id)))
                .join(Payment, User.id == Payment.user_id)
                .filter(
                    User.trial_used == True,
                    Payment.status == "paid",
                    Payment.amount > 0.0,
                )
            )
            converted_users = converted_users_res.scalar() or 0
            trial_to_paid_conversion = (converted_users / total_trial_users) * 100.0

        # 7. Renewal & Churn Rates
        expired_count_res = await self.db.execute(
            select(func.count(Subscription.id)).filter(
                Subscription.end_date < now
            )
        )
        total_expired = expired_count_res.scalar() or 0

        churn_rate = 0.0
        renewal_rate = 100.0
        if total_expired > 0:
            renewed_count_res = await self.db.execute(
                select(func.count(distinct(Subscription.user_id))).filter(
                    Subscription.end_date < now,
                    Subscription.user_id.in_(
                        select(Subscription.user_id).filter(
                            Subscription.end_date >= now, Subscription.is_active == True
                        )
                    ),
                )
            )
            total_renewed = renewed_count_res.scalar() or 0
            renewal_rate = (total_renewed / total_expired) * 100.0
            churn_rate = 100.0 - renewal_rate

        # 8. ARPU (Average Revenue Per User)
        arpu = 0.0
        if active_subscribers > 0:
            arpu = mrr / active_subscribers

        # 9. LTV (Lifetime Value)
        ltv = 0.0
        if churn_rate > 0:
            ltv = arpu / (churn_rate / 100.0)
        else:
            ltv = arpu * 12.0  # representing 1-year value estimate if churn is zero

        # 10. Coupon Conversion Rate
        coupon_red_res = await self.db.execute(select(func.count(CouponRedemption.id)))
        total_redemptions = coupon_red_res.scalar() or 0
        
        coupon_conv_rate = 0.0
        if total_redemptions > 0:
            successful_red_res = await self.db.execute(
                select(func.count(CouponRedemption.id))
                .join(Payment, CouponRedemption.payment_id == Payment.id)
                .filter(Payment.status == "paid")
            )
            successful_redemptions = successful_red_res.scalar() or 0
            coupon_conv_rate = (successful_redemptions / total_redemptions) * 100.0

        return {
            "mrr": round(mrr, 2),
            "arr": round(arr, 2),
            "arpu": round(arpu, 2),
            "ltv": round(ltv, 2),
            "active_subscribers": active_subscribers,
            "active_trials": active_trials,
            "total_trial_users": total_trial_users,
            "trial_to_paid_conversion_rate": round(trial_to_paid_conversion, 2),
            "renewal_rate": round(renewal_rate, 2),
            "churn_rate": round(churn_rate, 2),
            "coupon_conversion_rate": round(coupon_conv_rate, 2)
        }

    async def get_revenue_metrics(self) -> dict:
        """Calculate revenue split by Organic, Affiliate, and Referral channels."""
        # Total successful payments revenue
        total_rev_res = await self.db.execute(
            select(func.sum(Payment.amount)).filter(Payment.status == "paid")
        )
        total_revenue = total_rev_res.scalar() or 0.0

        # Affiliate revenue
        aff_rev_res = await self.db.execute(
            select(func.sum(Payment.amount))
            .join(AffiliateCommission, Payment.id == AffiliateCommission.payment_id)
            .filter(Payment.status == "paid")
        )
        affiliate_revenue = aff_rev_res.scalar() or 0.0

        # Referral revenue
        ref_rev_res = await self.db.execute(
            select(func.sum(Payment.amount))
            .join(User, Payment.user_id == User.id)
            .filter(
                Payment.status == "paid",
                User.referred_by_id.isnot(None),
                User.referred_by_id.not_in(select(Affiliate.user_id)),
            )
        )
        referral_revenue = ref_rev_res.scalar() or 0.0

        # Organic revenue
        organic_revenue = total_revenue - affiliate_revenue - referral_revenue

        return {
            "total_revenue": round(total_revenue, 2),
            "affiliate_revenue": round(affiliate_revenue, 2),
            "referral_revenue": round(referral_revenue, 2),
            "organic_revenue": round(max(0.0, organic_revenue), 2),
        }

    async def get_referral_metrics(self) -> dict:
        """Fetch general stats about referrals: invites count, rewards count, fraud flag count."""
        # Total referral invites
        codes_count_res = await self.db.execute(select(func.count(distinct(User.id))).filter(User.referred_by_id.isnot(None)))
        total_invites = codes_count_res.scalar() or 0

        # Group by status from referral_events
        events_by_status = await self.db.execute(
            select(ReferralEvent.status, func.count(ReferralEvent.id)).group_by(
                ReferralEvent.status
            )
        )
        status_breakdown = {status: count for status, count in events_by_status}

        return {
            "total_referrals": total_invites,
            "status_breakdown": status_breakdown,
        }

    async def get_churn_metrics(self) -> dict:
        """Get time series / overview of churn and renewal rates."""
        now = datetime.now(timezone.utc)
        
        expired_count_res = await self.db.execute(
            select(func.count(Subscription.id)).filter(
                Subscription.end_date < now
            )
        )
        total_expired = expired_count_res.scalar() or 0

        churn_rate = 0.0
        renewal_rate = 100.0
        if total_expired > 0:
            renewed_count_res = await self.db.execute(
                select(func.count(distinct(Subscription.user_id))).filter(
                    Subscription.end_date < now,
                    Subscription.user_id.in_(
                        select(Subscription.user_id).filter(
                            Subscription.end_date >= now, Subscription.is_active == True
                        )
                    ),
                )
            )
            total_renewed = renewed_count_res.scalar() or 0
            renewal_rate = (total_renewed / total_expired) * 100.0
            churn_rate = 100.0 - renewal_rate
            
        return {
            "total_expired_subscriptions": total_expired,
            "renewal_rate": round(renewal_rate, 2),
            "churn_rate": round(churn_rate, 2)
        }

    async def get_affiliate_analytics(self) -> dict:
        """Get analytics breakdown for affiliates."""
        # Total active affiliates
        active_aff_res = await self.db.execute(select(func.count(Affiliate.id)).filter(Affiliate.is_active == True))
        active_affiliates = active_aff_res.scalar() or 0
        
        # Total commissions paid
        comm_paid_res = await self.db.execute(
            select(func.sum(AffiliateCommission.amount)).filter(AffiliateCommission.status == "paid")
        )
        commissions_paid = comm_paid_res.scalar() or 0.0
        
        # Total commissions pending
        comm_pending_res = await self.db.execute(
            select(func.sum(AffiliateCommission.amount)).filter(AffiliateCommission.status == "pending")
        )
        commissions_pending = comm_pending_res.scalar() or 0.0
        
        # Total payouts paid out
        payouts_paid_res = await self.db.execute(
            select(func.sum(AffiliatePayout.amount)).filter(AffiliatePayout.status == "completed")
        )
        payouts_completed = payouts_paid_res.scalar() or 0.0
        
        return {
            "active_affiliates_count": active_affiliates,
            "commissions_paid": round(commissions_paid, 2),
            "commissions_pending": round(commissions_pending, 2),
            "payouts_completed": round(payouts_completed, 2)
        }

    async def get_trial_analytics(self) -> dict:
        """Get analytics breakdown for trials."""
        total_trial_users_res = await self.db.execute(
            select(func.count(User.id)).filter(User.trial_used == True)
        )
        total_trial_users = total_trial_users_res.scalar() or 0
        
        trial_package_res = await self.db.execute(
            select(ProductPackage.id).filter(ProductPackage.name == "Free Trial")
        )
        trial_package_id = trial_package_res.scalar()
        
        active_trials = 0
        now = datetime.now(timezone.utc)
        if trial_package_id:
            active_trials_res = await self.db.execute(
                select(func.count(Subscription.id)).filter(
                    Subscription.package_id == trial_package_id,
                    Subscription.is_active == True,
                    Subscription.end_date > now
                )
            )
            active_trials = active_trials_res.scalar() or 0
            
        trial_to_paid_conversion = 0.0
        if total_trial_users > 0:
            converted_users_res = await self.db.execute(
                select(func.count(distinct(User.id)))
                .join(Payment, User.id == Payment.user_id)
                .filter(
                    User.trial_used == True,
                    Payment.status == "paid",
                    Payment.amount > 0.0
                )
            )
            converted_users = converted_users_res.scalar() or 0
            trial_to_paid_conversion = (converted_users / total_trial_users) * 100.0
            
        return {
            "total_trial_claims": total_trial_users,
            "active_trials_count": active_trials,
            "trial_to_paid_conversion_rate": round(trial_to_paid_conversion, 2)
        }

    async def get_cohort_retention(self) -> list[dict]:
        """Calculate monthly cohort retention rates at 30, 60, 90, and 180 days."""
        query = """
        WITH user_cohorts AS (
            SELECT id AS user_id,
                   DATE_TRUNC('month', created_at) AS cohort_month,
                   created_at AS signup_date
            FROM users
        ),
        user_activities AS (
            SELECT DISTINCT s.user_id,
                   EXTRACT(EPOCH FROM (s.end_date - uc.signup_date)) / 86400.0 AS max_days_retained
            FROM subscriptions s
            JOIN user_cohorts uc ON s.user_id = uc.user_id
            WHERE s.is_active = true OR s.end_date > uc.signup_date
        )
        SELECT 
            c.cohort_month,
            COUNT(DISTINCT c.user_id) AS cohort_size,
            COUNT(DISTINCT CASE WHEN a.max_days_retained >= 30 THEN a.user_id END) AS retention_30d,
            COUNT(DISTINCT CASE WHEN a.max_days_retained >= 60 THEN a.user_id END) AS retention_60d,
            COUNT(DISTINCT CASE WHEN a.max_days_retained >= 90 THEN a.user_id END) AS retention_90d,
            COUNT(DISTINCT CASE WHEN a.max_days_retained >= 180 THEN a.user_id END) AS retention_180d
        FROM user_cohorts c
        LEFT JOIN user_activities a ON c.user_id = a.user_id
        GROUP BY c.cohort_month
        ORDER BY c.cohort_month DESC;
        """
        
        result = await self.db.execute(sa.text(query))
        cohorts = []
        for row in result:
            cohort_month_val = row[0]
            if isinstance(cohort_month_val, str):
                cohort_name = cohort_month_val[:7]
            elif hasattr(cohort_month_val, "strftime"):
                cohort_name = cohort_month_val.strftime("%Y-%m")
            else:
                cohort_name = str(cohort_month_val)[:7]
                
            size = row[1] or 0
            r30 = row[2] or 0
            r60 = row[3] or 0
            r90 = row[4] or 0
            r180 = row[5] or 0
            
            cohorts.append({
                "cohort_month": cohort_name,
                "cohort_size": size,
                "retention_30d": round((r30 / size * 100.0) if size > 0 else 0.0, 2),
                "retention_60d": round((r60 / size * 100.0) if size > 0 else 0.0, 2),
                "retention_90d": round((r90 / size * 100.0) if size > 0 else 0.0, 2),
                "retention_180d": round((r180 / size * 100.0) if size > 0 else 0.0, 2),
            })
        return cohorts

    async def calculate_and_save_all_risk_scores(self) -> None:
        """Calculate risk scores for all users and persist to user_risk_scores table."""
        from app.models.risk_score import UserRiskScore
        from app.models.event_log import EventLog
        
        now = datetime.now(timezone.utc)
        
        # Fetch all users
        user_res = await self.db.execute(select(User))
        users = user_res.scalars().all()
        
        for user in users:
            score = 0
            signals = {}
            
            # 1. Left Channel (+50 points)
            chan_event_res = await self.db.execute(
                select(EventLog.event_type)
                .filter(
                    EventLog.user_id == user.id,
                    EventLog.event_type.in_(["user_removed_from_channel", "user_rejoined_channel"])
                )
                .order_by(EventLog.created_at.desc())
                .limit(1)
            )
            latest_chan_event = chan_event_res.scalar()
            if latest_chan_event == "user_removed_from_channel":
                score += 50
                signals["left_channel"] = True
            
            # 2. Post-Trial Silence (+40 points)
            if user.trial_used:
                paid_res = await self.db.execute(
                    select(func.count(Payment.id)).filter(
                        Payment.user_id == user.id,
                        Payment.status == "paid",
                        Payment.amount > 0.0
                    )
                )
                paid_count = paid_res.scalar() or 0
                if paid_count == 0:
                    trial_pkg_res = await self.db.execute(
                        select(ProductPackage.id).filter(ProductPackage.name == "Free Trial")
                    )
                    trial_pkg_id = trial_pkg_res.scalar()
                    if trial_pkg_id:
                        trial_sub_res = await self.db.execute(
                            select(Subscription.end_date)
                            .filter(
                                Subscription.user_id == user.id,
                                Subscription.package_id == trial_pkg_id
                            )
                            .order_by(Subscription.end_date.desc())
                            .limit(1)
                        )
                        trial_end_date = trial_sub_res.scalar()
                        if trial_end_date:
                            trial_end_dt = trial_end_date.replace(tzinfo=timezone.utc) if trial_end_date.tzinfo is None else trial_end_date
                            if now - trial_end_dt > timedelta(days=5):
                                score += 40
                                signals["post_trial_silence"] = True

            # 3. Payment Delay (+30 points)
            active_sub_res = await self.db.execute(
                select(func.count(Subscription.id)).filter(
                    Subscription.user_id == user.id,
                    Subscription.is_active == True,
                    Subscription.end_date > now
                )
            )
            has_active = (active_sub_res.scalar() or 0) > 0
            if not has_active:
                expired_sub_res = await self.db.execute(
                    select(Subscription.end_date)
                    .filter(Subscription.user_id == user.id)
                    .order_by(Subscription.end_date.desc())
                    .limit(1)
                )
                last_end_date = expired_sub_res.scalar()
                if last_end_date:
                    last_end_dt = last_end_date.replace(tzinfo=timezone.utc) if last_end_date.tzinfo is None else last_end_date
                    if now - last_end_dt > timedelta(days=3):
                        score += 30
                        signals["payment_delay"] = True

            # 4. Grace Period (+15 points)
            if not has_active:
                expired_sub_res = await self.db.execute(
                    select(Subscription.end_date)
                    .filter(Subscription.user_id == user.id)
                    .order_by(Subscription.end_date.desc())
                    .limit(1)
                )
                last_end_date = expired_sub_res.scalar()
                if last_end_date:
                    last_end_dt = last_end_date.replace(tzinfo=timezone.utc) if last_end_date.tzinfo is None else last_end_date
                    if timedelta(days=0) < now - last_end_dt <= timedelta(days=1):
                        score += 15
                        signals["grace_period"] = True

            # 5. Low Activity / Silence (+20 points)
            last_event_res = await self.db.execute(
                select(EventLog.created_at)
                .filter(EventLog.user_id == user.id)
                .order_by(EventLog.created_at.desc())
                .limit(1)
            )
            last_event_time = last_event_res.scalar()
            if last_event_time:
                last_ev_dt = last_event_time.replace(tzinfo=timezone.utc) if last_event_time.tzinfo is None else last_event_time
                if now - last_ev_dt > timedelta(days=15):
                    score += 20
                    signals["low_activity"] = True
            else:
                score += 20
                signals["no_activity"] = True

            score = min(100, score)

            if score >= 90:
                segment = "CRITICAL"
            elif score >= 70:
                segment = "HIGH"
            elif score >= 40:
                segment = "MEDIUM"
            else:
                segment = "LOW"

            rs_res = await self.db.execute(
                select(UserRiskScore).filter(UserRiskScore.user_id == user.id)
            )
            risk_record = rs_res.scalars().first()
            if not risk_record:
                risk_record = UserRiskScore(user_id=user.id)
                self.db.add(risk_record)
            
            risk_record.risk_score = score
            risk_record.risk_segment = segment
            risk_record.signals_json = signals

        await self.db.flush()

    async def get_churn_risk_scores(self) -> list[dict]:
        """Fetch all user risk scores with user details."""
        from app.models.risk_score import UserRiskScore
        result = await self.db.execute(
            select(UserRiskScore, User)
            .join(User, UserRiskScore.user_id == User.id)
            .order_by(UserRiskScore.risk_score.desc())
        )
        scores = []
        for rs, user in result:
            scores.append({
                "user_id": rs.user_id,
                "telegram_id": user.telegram_id,
                "username": user.username,
                "risk_score": rs.risk_score,
                "risk_segment": rs.risk_segment,
                "signals": rs.signals_json,
                "updated_at": rs.updated_at.isoformat() if rs.updated_at else None
            })
        return scores

    async def refresh_materialized_views(self) -> None:
        """Refresh all SaaS materialized views concurrently to prevent read blocking."""
        views = [
            "mrr_daily_mv",
            "churn_rate_daily_mv",
            "cohort_retention_mv",
            "affiliate_revenue_mv",
            "campaign_conversion_mv"
        ]
        for view in views:
            try:
                await self.db.execute(sa.text(f"REFRESH MATERIALIZED VIEW CONCURRENTLY {view}"))
                logger.info(f"Successfully refreshed materialized view concurrently: {view}")
            except Exception as e:
                logger.warning(f"Concurrent refresh failed for {view}, attempting standard refresh: {e}")
                try:
                    await self.db.execute(sa.text(f"REFRESH MATERIALIZED VIEW {view}"))
                    logger.info(f"Successfully refreshed materialized view (standard): {view}")
                except Exception as ex:
                    logger.error(f"Failed to refresh materialized view {view}: {ex}")
        await self.db.commit()

