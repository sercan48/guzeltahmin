"""scale_sprint_migrations

Revision ID: 9c8d7e6f5a4b
Revises: a1b2c3d4e5f6
Create Date: 2026-05-30 00:20:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '9c8d7e6f5a4b'
down_revision: Union[str, Sequence[str], None] = 'a1b2c3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Add redis_msg_id to event_logs
    op.add_column("event_logs", sa.Column("redis_msg_id", sa.String(length=64), nullable=True))
    op.create_unique_constraint("uq_event_logs_redis_msg_id", "event_logs", ["redis_msg_id"])

    # 2. Add UniqueConstraint to campaign_executions
    op.create_unique_constraint("uq_user_campaign_event", "campaign_executions", ["user_id", "campaign_id", "triggered_by_event_id"])

    # 3. Create Materialized Views and Indexes
    op.execute("""
        CREATE MATERIALIZED VIEW mrr_daily_mv AS
        SELECT 
            CURRENT_DATE AS calculation_date,
            COALESCE(SUM((pp.price / NULLIF(pp.duration_days, 0)) * 30.0), 0.0) AS mrr
        FROM subscriptions s
        JOIN product_packages pp ON s.package_id = pp.id
        WHERE s.is_active = true AND s.end_date > NOW();
    """)
    op.execute("CREATE UNIQUE INDEX idx_mrr_daily_mv_date ON mrr_daily_mv (calculation_date);")

    op.execute("""
        CREATE MATERIALIZED VIEW churn_rate_daily_mv AS
        WITH expired_counts AS (
            SELECT COUNT(id) AS total_expired FROM subscriptions WHERE end_date < NOW()
        ),
        renewed_counts AS (
            SELECT COUNT(DISTINCT user_id) AS total_renewed 
            FROM subscriptions 
            WHERE end_date < NOW() AND user_id IN (
                SELECT user_id FROM subscriptions WHERE end_date >= NOW() AND is_active = true
            )
        )
        SELECT 
            CURRENT_DATE AS calculation_date,
            CASE 
                WHEN ec.total_expired > 0 THEN ROUND((1.0 - (rc.total_renewed::numeric / ec.total_expired::numeric)) * 100, 2)
                ELSE 0.0
            END AS churn_rate
        FROM expired_counts ec, renewed_counts rc;
    """)
    op.execute("CREATE UNIQUE INDEX idx_churn_daily_mv_date ON churn_rate_daily_mv (calculation_date);")

    op.execute("""
        CREATE MATERIALIZED VIEW cohort_retention_mv AS
        WITH user_cohorts AS (
            SELECT id AS user_id, DATE_TRUNC('month', created_at) AS cohort_month, created_at AS signup_date
            FROM users
        ),
        user_activities AS (
            SELECT DISTINCT s.user_id, EXTRACT(EPOCH FROM (s.end_date - uc.signup_date)) / 86400.0 AS max_days_retained
            FROM subscriptions s
            JOIN user_cohorts uc ON s.user_id = uc.user_id
            WHERE s.is_active = true OR s.end_date > uc.signup_date
        )
        SELECT 
            c.cohort_month,
            COUNT(DISTINCT c.user_id) AS cohort_size,
            ROUND(COALESCE((COUNT(DISTINCT CASE WHEN a.max_days_retained >= 30 THEN a.user_id END)::numeric / NULLIF(COUNT(DISTINCT c.user_id), 0) * 100), 0.0), 2) AS retention_30d,
            ROUND(COALESCE((COUNT(DISTINCT CASE WHEN a.max_days_retained >= 60 THEN a.user_id END)::numeric / NULLIF(COUNT(DISTINCT c.user_id), 0) * 100), 0.0), 2) AS retention_60d,
            ROUND(COALESCE((COUNT(DISTINCT CASE WHEN a.max_days_retained >= 90 THEN a.user_id END)::numeric / NULLIF(COUNT(DISTINCT c.user_id), 0) * 100), 0.0), 2) AS retention_90d,
            ROUND(COALESCE((COUNT(DISTINCT CASE WHEN a.max_days_retained >= 180 THEN a.user_id END)::numeric / NULLIF(COUNT(DISTINCT c.user_id), 0) * 100), 0.0), 2) AS retention_180d
        FROM user_cohorts c
        LEFT JOIN user_activities a ON c.user_id = a.user_id
        GROUP BY c.cohort_month;
    """)
    op.execute("CREATE UNIQUE INDEX idx_cohort_retention_mv_month ON cohort_retention_mv (cohort_month);")

    op.execute("""
        CREATE MATERIALIZED VIEW affiliate_revenue_mv AS
        SELECT 
            a.id AS affiliate_id,
            a.code AS affiliate_code,
            COALESCE(SUM(p.amount), 0.0) AS total_revenue,
            COALESCE(SUM(ac.amount), 0.0) AS commissions_generated
        FROM affiliates a
        LEFT JOIN affiliate_commissions ac ON a.id = ac.affiliate_id
        LEFT JOIN payments p ON ac.payment_id = p.id AND p.status = 'paid'
        GROUP BY a.id, a.code;
    """)
    op.execute("CREATE UNIQUE INDEX idx_aff_revenue_mv_id ON affiliate_revenue_mv (affiliate_id);")

    op.execute("""
        CREATE MATERIALIZED VIEW campaign_conversion_mv AS
        SELECT 
            ce.campaign_id,
            c.name AS campaign_name,
            COUNT(ce.id) AS total_sent,
            COUNT(CASE WHEN ce.status = 'converted' THEN 1 END) AS total_converted,
            CASE 
                WHEN COUNT(ce.id) > 0 THEN ROUND((COUNT(CASE WHEN ce.status = 'converted' THEN 1 END)::numeric / COUNT(ce.id)::numeric) * 100, 2)
                ELSE 0.0
            END AS conversion_rate
        FROM campaign_executions ce
        JOIN campaigns c ON ce.campaign_id = c.id
        WHERE ce.status IN ('sent', 'converted')
        GROUP BY ce.campaign_id, c.name;
    """)
    op.execute("CREATE UNIQUE INDEX idx_camp_conversion_mv_id ON campaign_conversion_mv (campaign_id);")


def downgrade() -> None:
    op.execute("DROP MATERIALIZED VIEW IF EXISTS campaign_conversion_mv;")
    op.execute("DROP MATERIALIZED VIEW IF EXISTS affiliate_revenue_mv;")
    op.execute("DROP MATERIALIZED VIEW IF EXISTS cohort_retention_mv;")
    op.execute("DROP MATERIALIZED VIEW IF EXISTS churn_rate_daily_mv;")
    op.execute("DROP MATERIALIZED VIEW IF EXISTS mrr_daily_mv;")
    op.drop_constraint("uq_user_campaign_event", "campaign_executions")
    op.drop_constraint("uq_event_logs_redis_msg_id", "event_logs")
    op.drop_column("event_logs", "redis_msg_id")
