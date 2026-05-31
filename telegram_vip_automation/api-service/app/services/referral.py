import logging
import random
import string
from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy import func

from app.models.user import User
from app.models.referral import ReferralCode, ReferralEvent, ReferralReward
from app.models.subscription import Subscription
from app.repositories.subscription import SubscriptionRepository

logger = logging.getLogger(__name__)


class ReferralService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.sub_repo = SubscriptionRepository(db)

    async def get_or_create_code(self, user_id: int) -> ReferralCode:
        """Fetch existing referral code or generate a new unique one."""
        result = await self.db.execute(
            select(ReferralCode).filter(ReferralCode.user_id == user_id)
        )
        ref_code = result.scalars().first()
        if ref_code:
            return ref_code

        # Generate a unique code (e.g. VIP8X72KD)
        while True:
            code = "VIP" + "".join(
                random.choices(string.ascii_uppercase + string.digits, k=6)
            )
            existing_res = await self.db.execute(
                select(ReferralCode).filter(ReferralCode.code == code)
            )
            if not existing_res.scalars().first():
                break

        ref_code = ReferralCode(user_id=user_id, code=code)
        self.db.add(ref_code)
        await self.db.flush()
        return ref_code

    async def log_referral_click(self, referred_telegram_id: int, referral_code_str: str) -> Tuple[bool, str]:
        """Log that a new user clicked a referral link.

        This sets the 'referred_by_id' on the user when they register or join.
        """
        # Fetch referral code owner
        result = await self.db.execute(
            select(ReferralCode).filter(ReferralCode.code == referral_code_str.strip().upper())
        )
        ref_code = result.scalars().first()
        if not ref_code:
            return False, "Referral code not found"

        # Fetch referrer
        result_referrer = await self.db.execute(
            select(User).filter(User.id == ref_code.user_id)
        )
        referrer = result_referrer.scalars().first()
        if not referrer:
            return False, "Referrer user not found"

        # Fetch referred user
        result_referred = await self.db.execute(
            select(User).filter(User.telegram_id == referred_telegram_id)
        )
        referred = result_referred.scalars().first()
        if not referred:
            return False, "Referred user not found (must register first)"

        # Self referral prevention
        if referrer.id == referred.id:
            return False, "Cannot refer yourself"

        # Update referred_by_id if not already set
        if referred.referred_by_id is None:
            referred.referred_by_id = referrer.id
            
            # Check if referral event already exists
            event_check = await self.db.execute(
                select(ReferralEvent).filter(ReferralEvent.referred_id == referred.id)
            )
            if not event_check.scalars().first():
                event = ReferralEvent(
                    referrer_id=referrer.id,
                    referred_id=referred.id,
                    status="pending",
                    risk_score=0.0,
                )
                self.db.add(event)
            
            await self.db.flush()
            return True, "Referral logged successfully"

        return False, "User already referred or organic"

    def _calculate_fraud_risk(self, referrer: User, referred: User) -> Tuple[float, str]:
        """Analyze names, usernames, and profiles to compute a risk score between 0.0 and 1.0."""
        score = 0.0
        reasons = []

        if referrer.telegram_id == referred.telegram_id:
            return 1.0, "Self referral attempt"

        ref_name = f"{referrer.first_name or ''} {referrer.last_name or ''}".strip().lower()
        refd_name = f"{referred.first_name or ''} {referred.last_name or ''}".strip().lower()

        if ref_name and refd_name and ref_name == refd_name:
            score += 0.6
            reasons.append("Exact name match")

        if not referred.username:
            score += 0.2
            reasons.append("No Telegram username")

        if not referred.first_name and not referred.last_name:
            score += 0.2
            reasons.append("No Telegram first or last name")

        return min(1.0, score), ", ".join(reasons)

    async def process_referral_on_payment(self, referred_user_id: int) -> None:
        """Evaluate referral eligibility upon first successful payment.

        If eligible, triggers fraud checks and processes reward.
        """
        # Fetch referred user
        referred = await self.db.get(User, referred_user_id)
        if not referred or not referred.referred_by_id:
            return

        # Fetch referrer
        referrer = await self.db.get(User, referred.referred_by_id)
        if not referrer:
            return

        # Check if this is indeed the first payment of the referred user
        from app.models.payment import Payment
        payments_res = await self.db.execute(
            select(func.count(Payment.id)).filter(
                Payment.user_id == referred_user_id, Payment.status == "paid"
            )
        )
        successful_payments = payments_res.scalar() or 0
        if successful_payments > 1:
            # Not the first payment
            return

        # Fetch referral event
        event_res = await self.db.execute(
            select(ReferralEvent).filter(
                ReferralEvent.referred_id == referred_user_id,
                ReferralEvent.referrer_id == referrer.id,
            )
        )
        event = event_res.scalars().first()
        if not event:
            # Create event if missing but referred_by_id was set
            event = ReferralEvent(
                referrer_id=referrer.id,
                referred_id=referred_user_id,
                status="pending",
                risk_score=0.0,
            )
            self.db.add(event)
            await self.db.flush()

        if event.status != "pending":
            return

        # Execute fraud checks
        risk_score, fraud_details = self._calculate_fraud_risk(referrer, referred)
        event.risk_score = risk_score
        event.fraud_details = fraud_details

        if risk_score >= 0.8:
            event.status = "fraud_flagged"
            logger.warning(
                "fraud_flagged_referral",
                extra={
                    "referrer": referrer.id,
                    "referred": referred.id,
                    "risk_score": risk_score,
                    "details": fraud_details,
                },
            )
            # Create a pending reward that won't be applied yet
            reward = ReferralReward(
                event_id=event.id,
                user_id=referrer.id,
                reward_type="free_days",
                reward_value=7,
                status="pending",
            )
            self.db.add(reward)
            await self.db.flush()
        else:
            event.status = "approved"
            # Grant reward immediately
            reward = ReferralReward(
                event_id=event.id,
                user_id=referrer.id,
                reward_type="free_days",
                reward_value=7,
                status="applied",
                applied_at=datetime.now(timezone.utc),
            )
            self.db.add(reward)
            await self.db.flush()

            # Apply reward: extend active subscription by 7 days
            active_sub = await self.sub_repo.get_active_by_user(referrer.id)
            if active_sub:
                active_sub.end_date += timedelta(days=7)
                logger.info(
                    "referral_reward_applied_extension",
                    extra={"user_id": referrer.id, "days": 7, "new_end_date": active_sub.end_date.isoformat()},
                )
            else:
                # Keep status as 'pending' to apply on next subscription start
                reward.status = "pending"
                reward.applied_at = None
                await self.db.flush()

    async def review_referral_event(self, event_id: int, approve: bool) -> bool:
        """Admin action: Manually approve or reject a fraud_flagged referral."""
        event = await self.db.get(ReferralEvent, event_id)
        if not event or event.status != "fraud_flagged":
            return False

        if approve:
            event.status = "approved"
            # Find the reward and apply it
            reward_res = await self.db.execute(
                select(ReferralReward).filter(
                    ReferralReward.event_id == event.id, ReferralReward.status == "pending"
                )
            )
            reward = reward_res.scalars().first()
            if reward:
                reward.status = "applied"
                reward.applied_at = datetime.now(timezone.utc)
                
                # Try applying extension
                active_sub = await self.sub_repo.get_active_by_user(event.referrer_id)
                if active_sub:
                    active_sub.end_date += timedelta(days=7)
                else:
                    reward.status = "pending"  # Wait for next subscription
                    reward.applied_at = None
        else:
            event.status = "rejected"
            reward_res = await self.db.execute(
                select(ReferralReward).filter(
                    ReferralReward.event_id == event.id, ReferralReward.status == "pending"
                )
            )
            reward = reward_res.scalars().first()
            if reward:
                reward.status = "voided"

        await self.db.flush()
        return True
