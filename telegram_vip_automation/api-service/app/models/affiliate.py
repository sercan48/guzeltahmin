from datetime import datetime
from sqlalchemy import DateTime, ForeignKey, String, Float, Boolean
from sqlalchemy.orm import Mapped, mapped_column, relationship
from typing import Optional
from app.models.base import Base


class Affiliate(Base):
    __tablename__ = "affiliates"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False)
    code: Mapped[str] = mapped_column(String(50), unique=True, index=True, nullable=False)
    commission_type: Mapped[str] = mapped_column(String(50), default="percentage", nullable=False)  # percentage, fixed
    commission_value: Mapped[float] = mapped_column(Float, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # Relationships
    user = relationship("User", foreign_keys=[user_id])


class AffiliateCommission(Base):
    __tablename__ = "affiliate_commissions"

    affiliate_id: Mapped[int] = mapped_column(ForeignKey("affiliates.id", ondelete="CASCADE"), nullable=False)
    payment_id: Mapped[int] = mapped_column(ForeignKey("payments.id", ondelete="CASCADE"), nullable=False)
    amount: Mapped[float] = mapped_column(Float, nullable=False)
    status: Mapped[str] = mapped_column(String(50), default="pending", nullable=False)  # pending, paid, voided

    # Relationships
    affiliate = relationship("Affiliate")
    payment = relationship("Payment")


class AffiliatePayout(Base):
    __tablename__ = "affiliate_payouts"

    affiliate_id: Mapped[int] = mapped_column(ForeignKey("affiliates.id", ondelete="CASCADE"), nullable=False)
    amount: Mapped[float] = mapped_column(Float, nullable=False)
    status: Mapped[str] = mapped_column(String(50), default="pending", nullable=False)  # pending, completed, cancelled
    paid_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    # Relationships
    affiliate = relationship("Affiliate")
