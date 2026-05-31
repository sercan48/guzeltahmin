import time
import logging
from typing import Dict, Optional
from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy import func, distinct, text

from app.db.session import get_db
from app.models.subscription import Subscription
from app.models.payment import Payment
from app.core.config import settings

try:
    import redis.asyncio as aioredis
except ImportError:
    aioredis = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)
router = APIRouter()

# In-memory counters (fallback/local metrics)
_metrics: Dict[str, float] = {
    "api_requests_total": 0,
    "api_errors_total": 0,
    "webhook_calls_total": 0,
    "webhook_success_total": 0,
    "webhook_fail_total": 0,
    "last_expiry_check_timestamp": 0,
}

_redis_client: Optional["aioredis.Redis"] = None  # type: ignore[name-defined]


async def _get_redis() -> Optional["aioredis.Redis"]:  # type: ignore[name-defined]
    global _redis_client
    if aioredis is None:
        return None
    if _redis_client is not None:
        return _redis_client
    if not settings.REDIS_URL:
        return None
    try:
        _redis_client = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
        return _redis_client
    except Exception as exc:
        logger.warning("Redis unavailable for metrics endpoint: %s", exc)
        return None


def inc(metric: str, value: float = 1.0):
    _metrics[metric] = _metrics.get(metric, 0) + value


def set_gauge(metric: str, value: float):
    _metrics[metric] = value


@router.get("/metrics")
async def prometheus_metrics(db: AsyncSession = Depends(get_db)):
    """Prometheus-compatible /metrics endpoint exposing optimized system KPIs."""
    
    # 1. Base DB Gauge Metrics
    active_subs = await db.execute(
        select(func.count()).select_from(Subscription).filter(Subscription.is_active == True)
    )
    active_count = active_subs.scalar() or 0

    total_payments = await db.execute(
        select(func.count()).select_from(Payment)
    )
    total_pay_count = total_payments.scalar() or 0

    paid_payments = await db.execute(
        select(func.count()).select_from(Payment).filter(Payment.status == "paid")
    )
    paid_count = paid_payments.scalar() or 0

    failed_payments = await db.execute(
        select(func.count()).select_from(Payment).filter(Payment.status == "failed")
    )
    failed_count = failed_payments.scalar() or 0

    pending_payments = await db.execute(
        select(func.count()).select_from(Payment).filter(Payment.status == "pending")
    )
    pending_count = pending_payments.scalar() or 0

    success_rate = round((paid_count / total_pay_count * 100), 2) if total_pay_count > 0 else 0.0

    # 2. Redis Metrics (Lag, Latencies, Retries, Reconciliation)
    redis = await _get_redis()
    redis_metrics = {}
    event_counts_redis = {}
    
    if redis:
        try:
            # Fetch all metrics keys
            keys = await redis.keys("metrics:*")
            for k in keys:
                # Only read scalar strings
                val_type = await redis.type(k)
                if val_type == "string":
                    val = await redis.get(k)
                    if val is not None:
                        redis_metrics[k] = val
                        
            # Event throughput from Redis hash to avoid event_logs full scan
            event_counts_redis = await redis.hgetall("metrics:hash:event_counts")
        except Exception as rex:
            logger.warning(f"Error fetching metrics from Redis: {rex}")

    event_queue_lag = float(redis_metrics.get("metrics:event_queue_lag", "0.0"))
    event_proc_latency = float(redis_metrics.get("metrics:latency:event_processing", "0.0"))

    # 3. Optimize Cohort Retention & Churn Ratio via Materialized Views
    cohort_data = []
    churn_rate_ratio = 0.0
    affiliate_revenues = []
    campaign_counts = []
    
    # ── MRR / Churn MV Query ──
    try:
        churn_mv_res = await db.execute(text("SELECT churn_rate FROM churn_rate_daily_mv LIMIT 1"))
        churn_row = churn_mv_res.first()
        if churn_row:
            churn_rate_ratio = float(churn_row[0]) / 100.0
    except Exception as e_churn:
        logger.warning(f"Failed to query churn_rate_daily_mv: {e_churn}. Using 0.0.")

    # ── Affiliate Revenue MV Query ──
    try:
        aff_mv_res = await db.execute(text("SELECT affiliate_id, total_revenue FROM affiliate_revenue_mv"))
        affiliate_revenues = aff_mv_res.all()
    except Exception as e_aff:
        logger.warning(f"Failed to query affiliate_revenue_mv: {e_aff}.")

    # ── Campaign Conversion MV Query ──
    try:
        camp_mv_res = await db.execute(text("SELECT campaign_name, total_sent, total_converted FROM campaign_conversion_mv"))
        campaign_counts = camp_mv_res.all()
    except Exception as e_camp:
        logger.warning(f"Failed to query campaign_conversion_mv: {e_camp}.")

    # ── Cohort Retention MV Query ──
    try:
        cohort_mv_res = await db.execute(
            text("SELECT cohort_month, cohort_size, retention_30d, retention_60d, retention_90d, retention_180d FROM cohort_retention_mv")
        )
        for row in cohort_mv_res:
            cohort_data.append({
                "cohort_month": row[0],
                "cohort_size": row[1] or 0,
                "retention_30d": float(row[2] or 0.0),
                "retention_60d": float(row[3] or 0.0),
                "retention_90d": float(row[4] or 0.0),
                "retention_180d": float(row[5] or 0.0)
            })
    except Exception as e_cohort:
        logger.warning(f"Failed to query cohort_retention_mv: {e_cohort}.")

    # Calculate average cohort retentions
    avg_30d = sum(c["retention_30d"] for c in cohort_data) / len(cohort_data) if cohort_data else 0.0
    avg_60d = sum(c["retention_60d"] for c in cohort_data) / len(cohort_data) if cohort_data else 0.0
    avg_90d = sum(c["retention_90d"] for c in cohort_data) / len(cohort_data) if cohort_data else 0.0
    avg_180d = sum(c["retention_180d"] for c in cohort_data) / len(cohort_data) if cohort_data else 0.0

    # ── Coupons, Referrals, and Notifications (remains direct/lightweight) ──
    from app.models.coupon import Coupon, CouponRedemption
    from app.models.referral import ReferralEvent
    from app.models.notification import NotificationLog
    
    coupon_usages = []
    try:
        coupon_usage_res = await db.execute(
            select(Coupon.code, Coupon.coupon_type, func.count(CouponRedemption.id))
            .join(Coupon, CouponRedemption.coupon_id == Coupon.id)
            .group_by(Coupon.code, Coupon.coupon_type)
        )
        coupon_usages = coupon_usage_res.all()
    except Exception:
        pass

    referral_conversions = []
    try:
        referral_conversions_res = await db.execute(
            select(ReferralEvent.referrer_id, ReferralEvent.status, func.count(ReferralEvent.id))
            .group_by(ReferralEvent.referrer_id, ReferralEvent.status)
        )
        referral_conversions = referral_conversions_res.all()
    except Exception:
        pass

    renewal_conversions = []
    try:
        renewal_conversions_res = await db.execute(
            select(NotificationLog.notification_type, NotificationLog.status, func.count(NotificationLog.id))
            .group_by(NotificationLog.notification_type, NotificationLog.status)
        )
        renewal_conversions = renewal_conversions_res.all()
    except Exception:
        pass

    # Risk segment counts
    risk_distribution = []
    try:
        from app.models.risk_score import UserRiskScore
        risk_dist_res = await db.execute(
            select(UserRiskScore.risk_segment, func.count(UserRiskScore.id)).group_by(UserRiskScore.risk_segment)
        )
        risk_distribution = risk_dist_res.all()
    except Exception:
        pass

    # Affiliate payout volume
    payout_volumes = []
    try:
        from app.models.affiliate import AffiliatePayout
        payout_vol_res = await db.execute(
            select(AffiliatePayout.status, func.sum(AffiliatePayout.amount)).group_by(AffiliatePayout.status)
        )
        payout_volumes = payout_vol_res.all()
    except Exception:
        pass

    # Assemble Prometheus Metrics payload
    lines = [
        "# HELP vip_active_subscriptions Current active subscription count",
        "# TYPE vip_active_subscriptions gauge",
        f"vip_active_subscriptions {active_count}",
        "",
        "# HELP vip_payments_total Total payment records by status",
        "# TYPE vip_payments_total gauge",
        f'vip_payments_total{{status="paid"}} {paid_count}',
        f'vip_payments_total{{status="failed"}} {failed_count}',
        f'vip_payments_total{{status="pending"}} {pending_count}',
        "",
        "# HELP vip_payment_success_rate Payment success percentage",
        "# TYPE vip_payment_success_rate gauge",
        f"vip_payment_success_rate {success_rate}",
        "",
        "# HELP vip_webhook_calls_total Total webhook invocations",
        "# TYPE vip_webhook_calls_total counter",
        f"vip_webhook_calls_total {_metrics.get('webhook_calls_total', 0)}",
        "",
        "# HELP vip_webhook_success_total Successful webhook confirmations",
        "# TYPE vip_webhook_success_total counter",
        f"vip_webhook_success_total {_metrics.get('webhook_success_total', 0)}",
        "",
        "# HELP vip_webhook_fail_total Failed webhook attempts",
        "# TYPE vip_webhook_fail_total counter",
        f"vip_webhook_fail_total {_metrics.get('webhook_fail_total', 0)}",
        "",
        "# HELP vip_api_requests_total Total API requests served",
        "# TYPE vip_api_requests_total counter",
        f"vip_api_requests_total {_metrics.get('api_requests_total', 0)}",
        "",
        "# HELP vip_api_errors_total Total API error responses",
        "# TYPE vip_api_errors_total counter",
        f"vip_api_errors_total {_metrics.get('api_errors_total', 0)}",
        "",
        "# HELP vip_last_expiry_check Unix timestamp of last expiry check run",
        "# TYPE vip_last_expiry_check gauge",
        f"vip_last_expiry_check {_metrics.get('last_expiry_check_timestamp', 0)}",
        "",
        "# HELP vip_event_queue_lag_seconds Time delay of oldest pending event stream item",
        "# TYPE vip_event_queue_lag_seconds gauge",
        f"vip_event_queue_lag_seconds {event_queue_lag}",
        "",
        "# HELP vip_event_processing_latency_seconds Time taken to ingest event to PG",
        "# TYPE vip_event_processing_latency_seconds gauge",
        f"vip_event_processing_latency_seconds {event_proc_latency}",
        "",
        "# HELP vip_coupon_usage_total Total coupon usage counts",
        "# TYPE vip_coupon_usage_total counter",
    ]

    for code, coupon_type, count in coupon_usages:
        lines.append(f'vip_coupon_usage_total{{code="{code}",type="{coupon_type}",status="redeemed"}} {count}')
    lines.append("")

    lines.extend([
        "# HELP vip_referral_conversions_total Total referral conversions",
        "# TYPE vip_referral_conversions_total counter",
    ])
    for referrer_id, status, count in referral_conversions:
        lines.append(f'vip_referral_conversions_total{{referrer_id="{referrer_id}",status="{status}"}} {count}')
    lines.append("")

    lines.extend([
        "# HELP vip_renewal_conversions_total Total renewal notifications sent",
        "# TYPE vip_renewal_conversions_total counter",
    ])
    for notification_type, status, count in renewal_conversions:
        lines.append(f'vip_renewal_conversions_total{{notification_type="{notification_type}",status="{status}"}} {count}')
    lines.append("")

    lines.extend([
        "# HELP vip_affiliate_revenue_total Total revenue generated by affiliate",
        "# TYPE vip_affiliate_revenue_total counter",
    ])
    for affiliate_id, revenue in affiliate_revenues:
        lines.append(f'vip_affiliate_revenue_total{{affiliate_id="{affiliate_id}"}} {revenue:.2f}')
    lines.append("")

    lines.extend([
        "# HELP vip_churn_rate_ratio Current ratio of churned subscriptions",
        "# TYPE vip_churn_rate_ratio gauge",
        f"vip_churn_rate_ratio {churn_rate_ratio:.4f}",
        ""
    ])

    # 4. Event throughput from Redis hash or fallback to direct DB count if empty
    lines.extend([
        "# HELP vip_event_throughput_total Total count of logged events by type",
        "# TYPE vip_event_throughput_total counter"
    ])
    if event_counts_redis:
        for event_type, count in event_counts_redis.items():
            lines.append(f'vip_event_throughput_total{{event_type="{event_type}",source="redis"}} {count}')
    else:
        # Fallback to direct DB query (lightweight if empty, but handled safely)
        try:
            from app.models.event_log import EventLog
            event_counts_res = await db.execute(
                select(EventLog.event_type, func.count(EventLog.id)).group_by(EventLog.event_type)
            )
            for event_type, count in event_counts_res.all():
                lines.append(f'vip_event_throughput_total{{event_type="{event_type}",source="postgres"}} {count}')
        except Exception:
            pass
    lines.append("")

    # 5. Campaign execution
    lines.extend([
        "# HELP vip_campaign_executions_total Total campaign executions by status",
        "# TYPE vip_campaign_executions_total counter"
    ])
    for camp_name, total_s, total_c in campaign_counts:
        lines.append(f'vip_campaign_executions_total{{campaign="{camp_name}",status="sent"}} {total_s}')
        lines.append(f'vip_campaign_executions_total{{campaign="{camp_name}",status="converted"}} {total_c}')
    lines.append("")

    # 6. Cohort retention averages
    lines.extend([
        "# HELP vip_average_cohort_retention_ratio Average cohort retention ratio by days since signup",
        "# TYPE vip_average_cohort_retention_ratio gauge",
        f'vip_average_cohort_retention_ratio{{period="30d"}} {avg_30d / 100.0:.4f}',
        f'vip_average_cohort_retention_ratio{{period="60d"}} {avg_60d / 100.0:.4f}',
        f'vip_average_cohort_retention_ratio{{period="90d"}} {avg_90d / 100.0:.4f}',
        f'vip_average_cohort_retention_ratio{{period="180d"}} {avg_180d / 100.0:.4f}',
        ""
    ])

    # 7. Churn score distribution
    lines.extend([
        "# HELP vip_churn_risk_segment_users Active users count by risk segment",
        "# TYPE vip_churn_risk_segment_users gauge"
    ])
    for segment, count in risk_distribution:
        lines.append(f'vip_churn_risk_segment_users{{segment="{segment}"}} {count}')
    lines.append("")

    # 8. Affiliate payout volume
    lines.extend([
        "# HELP vip_affiliate_payout_volume_total Sum of payouts by status",
        "# TYPE vip_affiliate_payout_volume_total gauge"
    ])
    for status, amount in payout_volumes:
        lines.append(f'vip_affiliate_payout_volume_total{{status="{status}"}} {amount or 0.0:.2f}')
    lines.append("")

    # 9. Ingest custom redis metrics (Worker Retries, Job Success Rates, Reconciliation Fixes)
    # Loop over redis_metrics and add format-compliant metrics to response
    for k, v in redis_metrics.items():
        if k.startswith("metrics:counter:worker_job_success_rate:"):
            # format: metrics:counter:worker_job_success_rate:job_name:status
            parts = k.split(":")
            if len(parts) >= 6:
                job_name = parts[4]
                status = parts[5]
                lines.append(f'vip_worker_job_success_rate{{job_name="{job_name}",status="{status}"}} {v}')
        elif k.startswith("metrics:counter:worker_retry_count:"):
            # format: metrics:counter:worker_retry_count:job_name
            parts = k.split(":")
            if len(parts) >= 5:
                job_name = parts[4]
                lines.append(f'vip_worker_retry_total{{job_name="{job_name}"}} {v}')
        elif k.startswith("metrics:counter:reconciliation_fix_count:"):
            # format: metrics:counter:reconciliation_fix_count:type
            parts = k.split(":")
            if len(parts) >= 5:
                fix_type = parts[4]
                lines.append(f'vip_reconciliation_fix_total{{type="{fix_type}"}} {v}')
        elif k == "metrics:counter:campaign_dedup_rate":
            lines.append(f"vip_campaign_dedup_total {v}")

    return "\n".join(lines)
