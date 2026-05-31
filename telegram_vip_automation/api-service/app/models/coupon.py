from datetime import datetime
from sqlalchemy import DateTime, ForeignKey, String, Integer, Float, Boolean, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from typing import Optional
from app.models.base import Base


class Coupon(Base):
    __tablename__ = "coupons"

    code: Mapped[str] = mapped_column(String(50), unique=True, index=True, nullable=False)
    coupon_type: Mapped[str] = mapped_column(String(50), nullable=False)  # percentage, fixed, free_trial
    value: Mapped[float] = mapped_column(Float, nullable=False)
    is_first_purchase_only: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    start_date: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    end_date: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    max_usage: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    current_usage: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    per_user_limit: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class CouponRedemption(Base):
    __tablename__ = "coupon_redemptions"

    coupon_id: Mapped[int] = mapped_column(ForeignKey("coupons.id", ondelete="CASCADE"), nullable=False)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    payment_id: Mapped[Optional[int]] = mapped_column(ForeignKey("payments.id", ondelete="SET NULL"), nullable=True)

    # Relationships
    coupon = relationship("Coupon")
    user = relationship("User")
    payment = relationship("Payment")

    __table_args__ = (
        UniqueConstraint("coupon_id", "user_id", name="uq_coupon_user_redemption"),
    )
