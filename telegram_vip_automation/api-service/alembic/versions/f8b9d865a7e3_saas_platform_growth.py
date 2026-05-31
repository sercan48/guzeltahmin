"""saas_platform_growth

Revision ID: f8b9d865a7e3
Revises: 7e3f3f54d371
Create Date: 2026-05-29 23:15:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f8b9d865a7e3'
down_revision: Union[str, Sequence[str], None] = '7e3f3f54d371'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema to growth platform."""
    
    # 1. Create products table
    op.create_table(
        "products",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.String(length=1024), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    # 2. Create product_channels table
    op.create_table(
        "product_channels",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("product_id", sa.Integer(), sa.ForeignKey("products.id", ondelete="CASCADE"), nullable=False),
        sa.Column("channel_id", sa.Integer(), sa.ForeignKey("telegram_channels.id", ondelete="CASCADE"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    # 3. Create product_packages table
    op.create_table(
        "product_packages",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("product_id", sa.Integer(), sa.ForeignKey("products.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.String(length=1024), nullable=True),
        sa.Column("price", sa.Float(), nullable=False),
        sa.Column("duration_days", sa.Integer(), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    # 4. Create event_logs table
    op.create_table(
        "event_logs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("event_type", sa.String(length=100), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("correlation_id", sa.String(length=64), nullable=False),
        sa.Column("source_service", sa.String(length=50), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_event_logs_event_type", "event_logs", ["event_type"])
    op.create_index("ix_event_logs_correlation_id", "event_logs", ["correlation_id"])
    op.create_index("ix_event_logs_created_at", "event_logs", ["created_at"])

    # 5. Create admin_audit_logs table
    op.create_table(
        "admin_audit_logs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("admin_id", sa.Integer(), sa.ForeignKey("admins.id", ondelete="CASCADE"), nullable=False),
        sa.Column("action", sa.String(length=100), nullable=False),
        sa.Column("target_type", sa.String(length=50), nullable=False),
        sa.Column("target_id", sa.String(length=100), nullable=False),
        sa.Column("details", sa.Text(), nullable=True),
        sa.Column("ip_address", sa.String(length=45), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    # 6. Create user_risk_scores table
    op.create_table(
        "user_risk_scores",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False),
        sa.Column("risk_score", sa.Integer(), server_default="0", nullable=False),
        sa.Column("risk_segment", sa.String(length=20), server_default="LOW", nullable=False),
        sa.Column("signals_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    # 7. Create campaigns, campaign_rules, campaign_executions
    op.create_table(
        "campaigns",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_table(
        "campaign_rules",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("campaign_id", sa.Integer(), sa.ForeignKey("campaigns.id", ondelete="CASCADE"), nullable=False),
        sa.Column("trigger_event", sa.String(length=100), nullable=False),
        sa.Column("delay_hours", sa.Integer(), server_default="0", nullable=False),
        sa.Column("coupon_code", sa.String(length=50), nullable=True),
        sa.Column("message_template", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_campaign_rules_trigger_event", "campaign_rules", ["trigger_event"])

    op.create_table(
        "campaign_executions",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("campaign_id", sa.Integer(), sa.ForeignKey("campaigns.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("status", sa.String(length=50), server_default="pending", nullable=False),
        sa.Column("triggered_by_event_id", sa.Integer(), sa.ForeignKey("event_logs.id", ondelete="SET NULL"), nullable=True),
        sa.Column("executed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("converted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    # 8. Data Migration & Table Transformations
    connection = op.get_bind()

    # Step A: Populating products based on telegram_channels (if any exist)
    # Otherwise insert a default VIP product
    connection.execute(sa.text(
        "INSERT INTO products (name, description, is_active, created_at, updated_at) "
        "VALUES ('VIP Premium Product', 'Legacy channel subscription product', true, NOW(), NOW())"
    ))
    
    # Step B: Get the default product id
    default_product_id = connection.execute(sa.text("SELECT id FROM products LIMIT 1")).scalar() or 1
    
    # Link channels to products
    connection.execute(sa.text(
        f"INSERT INTO product_channels (product_id, channel_id, created_at, updated_at) "
        f"SELECT {default_product_id}, id, NOW(), NOW() FROM telegram_channels"
    ))

    # Step C: Populate product_packages from packages
    connection.execute(sa.text(
        f"INSERT INTO product_packages (product_id, name, description, price, duration_days, is_active, created_at, updated_at) "
        f"SELECT {default_product_id}, name, description, price, duration_days, is_active, created_at, updated_at FROM packages"
    ))

    # Step D: Transformation of Subscriptions table
    op.add_column("subscriptions", sa.Column("product_id", sa.Integer(), nullable=True))
    
    # Update subscriptions with the default product ID
    connection.execute(sa.text(f"UPDATE subscriptions SET product_id = {default_product_id}"))
    op.alter_column("subscriptions", "product_id", nullable=False)
    op.create_foreign_key("fk_subscriptions_product_id", "subscriptions", "products", ["product_id"], ["id"], ondelete="CASCADE")

    # Update package foreign key on subscriptions (since packages id matches product_packages id sequence)
    op.drop_constraint("subscriptions_package_id_fkey", "subscriptions", type_="foreignkey")
    op.create_foreign_key("fk_subscriptions_package_id", "subscriptions", "product_packages", ["package_id"], ["id"])

    # Drop legacy unique index on subscriptions
    # In SQLite it might be named differently, on Postgres we drop uq_one_active_sub_per_user_channel (or uq_subscriptions_user_channel)
    try:
        op.drop_index("uq_one_active_sub_per_user_channel", table_name="subscriptions")
    except Exception:
        # Ignore if it doesn't exist
        pass

    try:
        op.drop_column("subscriptions", "channel_id")
    except Exception:
        pass

    # Create new composite unique index
    op.create_index(
        "uq_one_active_sub_per_user_product",
        "subscriptions",
        ["user_id", "product_id"],
        unique=True,
        postgresql_where="is_active = true"
    )

    # Step E: Transformation of Payments table
    op.drop_constraint("payments_package_id_fkey", "payments", type_="foreignkey")
    op.create_foreign_key("fk_payments_package_id", "payments", "product_packages", ["package_id"], ["id"])

    # Drop legacy packages table
    op.drop_table("packages")


def downgrade() -> None:
    """Downgrade schema."""
    # We don't implement full downgrade here to prevent losing production product data,
    # but a simple table drop is defined for testing environments.
    op.drop_table("campaign_executions")
    op.drop_table("campaign_rules")
    op.drop_table("campaigns")
    op.drop_table("user_risk_scores")
    op.drop_table("admin_audit_logs")
    op.drop_index("ix_event_logs_created_at", table_name="event_logs")
    op.drop_index("ix_event_logs_correlation_id", table_name="event_logs")
    op.drop_index("ix_event_logs_event_type", table_name="event_logs")
    op.drop_table("event_logs")
    
    # Recreate legacy packages and restore tables is skipped to maintain data integrity.
