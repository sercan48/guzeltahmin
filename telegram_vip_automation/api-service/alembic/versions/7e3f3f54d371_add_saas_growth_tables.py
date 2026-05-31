"""add_saas_growth_tables

Revision ID: 7e3f3f54d371
Revises: 
Create Date: 2026-05-29 22:43:06.565740

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '7e3f3f54d371'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # 1. Update users table with new columns
    op.add_column("users", sa.Column("trial_used", sa.Boolean(), server_default="false", nullable=False))
    op.add_column("users", sa.Column("referred_by_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True))

    # 2. Create coupons table
    op.create_table(
        "coupons",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("code", sa.String(length=50), nullable=False),
        sa.Column("coupon_type", sa.String(length=50), nullable=False),
        sa.Column("value", sa.Float(), nullable=False),
        sa.Column("is_first_purchase_only", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("start_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("end_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("max_usage", sa.Integer(), nullable=True),
        sa.Column("current_usage", sa.Integer(), server_default="0", nullable=False),
        sa.Column("per_user_limit", sa.Integer(), server_default="1", nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_coupons_code", "coupons", ["code"], unique=True)

    # 3. Create coupon_redemptions table
    op.create_table(
        "coupon_redemptions",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("coupon_id", sa.Integer(), sa.ForeignKey("coupons.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("payment_id", sa.Integer(), sa.ForeignKey("payments.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("coupon_id", "user_id", name="uq_coupon_user_redemption")
    )

    # 4. Create referral_codes table
    op.create_table(
        "referral_codes",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("code", sa.String(length=50), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("user_id", name="uq_referral_codes_user_id")
    )
    op.create_index("ix_referral_codes_code", "referral_codes", ["code"], unique=True)

    # 5. Create referral_events table
    op.create_table(
        "referral_events",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("referrer_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("referred_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("status", sa.String(length=50), server_default="pending", nullable=False),
        sa.Column("risk_score", sa.Float(), server_default="0.0", nullable=False),
        sa.Column("fraud_details", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("referred_id", name="uq_referral_events_referred_id")
    )

    # 6. Create referral_rewards table
    op.create_table(
        "referral_rewards",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("event_id", sa.Integer(), sa.ForeignKey("referral_events.id", ondelete="SET NULL"), nullable=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("reward_type", sa.String(length=50), server_default="free_days", nullable=False),
        sa.Column("reward_value", sa.Integer(), server_default="7", nullable=False),
        sa.Column("status", sa.String(length=50), server_default="pending", nullable=False),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    # 7. Create affiliates table
    op.create_table(
        "affiliates",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("code", sa.String(length=50), nullable=False),
        sa.Column("commission_type", sa.String(length=50), server_default="percentage", nullable=False),
        sa.Column("commission_value", sa.Float(), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("user_id", name="uq_affiliates_user_id")
    )
    op.create_index("ix_affiliates_code", "affiliates", ["code"], unique=True)

    # 8. Create affiliate_commissions table
    op.create_table(
        "affiliate_commissions",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("affiliate_id", sa.Integer(), sa.ForeignKey("affiliates.id", ondelete="CASCADE"), nullable=False),
        sa.Column("payment_id", sa.Integer(), sa.ForeignKey("payments.id", ondelete="CASCADE"), nullable=False),
        sa.Column("amount", sa.Float(), nullable=False),
        sa.Column("status", sa.String(length=50), server_default="pending", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    # 9. Create affiliate_payouts table
    op.create_table(
        "affiliate_payouts",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("affiliate_id", sa.Integer(), sa.ForeignKey("affiliates.id", ondelete="CASCADE"), nullable=False),
        sa.Column("amount", sa.Float(), nullable=False),
        sa.Column("status", sa.String(length=50), server_default="pending", nullable=False),
        sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    # 10. Create notification_logs table
    op.create_table(
        "notification_logs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("subscription_id", sa.Integer(), sa.ForeignKey("subscriptions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("notification_type", sa.String(length=50), nullable=False),
        sa.Column("status", sa.String(length=50), server_default="sent", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_notification_sub_type", "notification_logs", ["subscription_id", "notification_type"])

    # 11. Create admins table
    op.create_table(
        "admins",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("username", sa.String(length=100), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("role", sa.String(length=50), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_admins_username", "admins", ["username"], unique=True)

    # 12. Add coupon_id to payments table
    op.add_column("payments", sa.Column("coupon_id", sa.Integer(), sa.ForeignKey("coupons.id", ondelete="SET NULL"), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    # Drop coupon_id from payments
    op.drop_constraint("payments_coupon_id_fkey", "payments", type_="foreignkey")
    op.drop_column("payments", "coupon_id")

    op.drop_index("ix_admins_username", table_name="admins")
    op.drop_table("admins")
    op.drop_index("ix_notification_sub_type", table_name="notification_logs")
    op.drop_table("notification_logs")
    op.drop_table("affiliate_payouts")
    op.drop_table("affiliate_commissions")
    op.drop_index("ix_affiliates_code", table_name="affiliates")
    op.drop_table("affiliates")
    op.drop_table("referral_rewards")
    op.drop_table("referral_events")
    op.drop_index("ix_referral_codes_code", table_name="referral_codes")
    op.drop_table("referral_codes")
    op.drop_table("coupon_redemptions")
    op.drop_index("ix_coupons_code", table_name="coupons")
    op.drop_table("coupons")
    
    # Remove columns from users
    op.drop_constraint("users_referred_by_id_fkey", "users", type_="foreignkey")
    op.drop_column("users", "referred_by_id")
    op.drop_column("users", "trial_used")
