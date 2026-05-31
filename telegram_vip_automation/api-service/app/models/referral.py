from datetime import datetime
from sqlalchemy import DateTime, ForeignKey, String, Integer, Float, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from typing import Optional
from app.models.base import Base


class ReferralCode(Base):
    __tablename__ = "referral_codes"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False)
    code: Mapped[str] = mapped_column(String(50), unique=True, index=True, nullable=False)

    # Relationship
    user = relationship("User", foreign_keys=[user_id])


class ReferralEvent(Base):
    __tablename__ = "referral_events"

    referrer_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    referred_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False)
    status: Mapped[str] = mapped_column(String(50), default="pending", nullable=False)  # pending, qualified, fraud_flagged, rejected, approved
    risk_score: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    fraud_details: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Relationships
    referrer = relationship("User", foreign_keys=[referrer_id])
    referred = relationship("User", foreign_keys=[referred_id])


class ReferralReward(Base):
    __tablename__ = "referral_rewards"

    event_id: Mapped[Optional[int]] = mapped_column(ForeignKey("referral_events.id", ondelete="SET NULL"), nullable=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    reward_type: Mapped[str] = mapped_column(String(50), default="free_days", nullable=False)
    reward_value: Mapped[int] = mapped_column(Integer, default=7, nullable=False)
    status: Mapped[str] = mapped_column(String(50), default="pending", nullable=False)  # pending, applied, voided
    applied_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    # Relationships
    user = relationship("User", foreign_keys=[user_id])
    event = relationship("ReferralEvent", foreign_keys=[event_id])
