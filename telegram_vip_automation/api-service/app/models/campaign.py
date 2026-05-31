from sqlalchemy import String, Boolean, ForeignKey, Integer, Text, DateTime, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from typing import List, Optional, TYPE_CHECKING
from datetime import datetime
from app.models.base import Base

if TYPE_CHECKING:
    from app.models.user import User
    from app.models.event_log import EventLog


class Campaign(Base):
    __tablename__ = "campaigns"

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # Relationships
    rules: Mapped[List["CampaignRule"]] = relationship(back_populates="campaign", cascade="all, delete-orphan")
    executions: Mapped[List["CampaignExecution"]] = relationship(back_populates="campaign", cascade="all, delete-orphan")


class CampaignRule(Base):
    __tablename__ = "campaign_rules"

    campaign_id: Mapped[int] = mapped_column(ForeignKey("campaigns.id", ondelete="CASCADE"), nullable=False)
    trigger_event: Mapped[str] = mapped_column(String(100), index=True, nullable=False)  # e.g. trial_expired
    delay_hours: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    coupon_code: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    message_template: Mapped[str] = mapped_column(Text, nullable=False)

    # Relationships
    campaign: Mapped["Campaign"] = relationship(back_populates="rules")


class CampaignExecution(Base):
    __tablename__ = "campaign_executions"
    __table_args__ = (
        UniqueConstraint("user_id", "campaign_id", "triggered_by_event_id", name="uq_user_campaign_event"),
    )

    campaign_id: Mapped[int] = mapped_column(ForeignKey("campaigns.id", ondelete="CASCADE"), nullable=False)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    status: Mapped[str] = mapped_column(String(50), default="pending", nullable=False)  # pending, sent, failed, converted
    triggered_by_event_id: Mapped[Optional[int]] = mapped_column(ForeignKey("event_logs.id", ondelete="SET NULL"), nullable=True)
    executed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    converted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    # Relationships
    campaign: Mapped["Campaign"] = relationship(back_populates="executions")

