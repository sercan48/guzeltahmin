from sqlalchemy import ForeignKey, Float, String
from sqlalchemy.orm import Mapped, mapped_column, relationship
from typing import TYPE_CHECKING
from app.models.base import Base

if TYPE_CHECKING:
    from app.models.user import User
    from app.models.package import Package


class Payment(Base):
    __tablename__ = "payments"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    package_id: Mapped[int] = mapped_column(ForeignKey("packages.id"), nullable=False)
    
    amount: Mapped[float] = mapped_column(Float, nullable=False)
    currency: Mapped[str] = mapped_column(String(10), default="TRY", nullable=False)
    
    # State tracking: pending, paid, failed, refunded
    status: Mapped[str] = mapped_column(String(50), default="pending", nullable=False)
    
    provider_tx_id: Mapped[str] = mapped_column(String(255), unique=True, nullable=True)

    # Relationships
    user: Mapped["User"] = relationship(back_populates="payments")
    package: Mapped["Package"] = relationship(back_populates="payments")
