from sqlalchemy import ForeignKey, Float, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from typing import Optional, TYPE_CHECKING
from app.models.base import Base

if TYPE_CHECKING:
    from app.models.user import User
    from app.models.product import ProductPackage


VALID_PAYMENT_TRANSITIONS: dict[str, set[str]] = {
    "pending": {"processing", "failed"},
    "processing": {"paid", "failed"},
    "paid": {"refunded"},
    "failed": set(),
    "refunded": set(),
}


class InvalidPaymentTransition(ValueError):
    """Raised when an invalid payment status transition is attempted."""


class Payment(Base):
    __tablename__ = "payments"

    __table_args__ = (
        UniqueConstraint("provider_tx_id", name="uq_payments_provider_tx_id"),
        UniqueConstraint("idempotency_key", name="uq_payments_idempotency_key"),
        Index("ix_payments_user_status", "user_id", "status"),
    )

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    package_id: Mapped[int] = mapped_column(ForeignKey("product_packages.id"), nullable=False)

    amount: Mapped[float] = mapped_column(Float, nullable=False)
    currency: Mapped[str] = mapped_column(String(10), default="TRY", nullable=False)

    status: Mapped[str] = mapped_column(String(50), default="pending", nullable=False)

    provider_tx_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    coupon_id: Mapped[Optional[int]] = mapped_column(ForeignKey("coupons.id", ondelete="SET NULL"), nullable=True)

    # Relationships
    user: Mapped["User"] = relationship(back_populates="payments")
    package: Mapped["ProductPackage"] = relationship(back_populates="payments")

    def transition_to(self, new_status: str) -> None:
        """Enforce state machine transitions.

        Valid transitions:
            pending → processing | failed
            processing → paid | failed
            paid → refunded
        """
        allowed = VALID_PAYMENT_TRANSITIONS.get(self.status, set())
        if new_status not in allowed:
            raise InvalidPaymentTransition(
                f"Cannot transition payment from '{self.status}' to '{new_status}'"
            )
        self.status = new_status
