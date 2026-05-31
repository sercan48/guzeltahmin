from datetime import datetime
from sqlalchemy import DateTime, ForeignKey, Boolean, Index
from sqlalchemy.orm import Mapped, mapped_column, relationship
from typing import TYPE_CHECKING
from app.models.base import Base

if TYPE_CHECKING:
    from app.models.user import User
    from app.models.product import Product, ProductPackage


class Subscription(Base):
    __tablename__ = "subscriptions"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"), nullable=False)
    package_id: Mapped[int] = mapped_column(ForeignKey("product_packages.id"), nullable=False)

    start_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    end_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # Relationships
    user: Mapped["User"] = relationship(back_populates="subscriptions")
    product: Mapped["Product"] = relationship(back_populates="subscriptions")
    package: Mapped["ProductPackage"] = relationship(back_populates="subscriptions")

    __table_args__ = (
        # Prevent duplicate active subscriptions per user per product
        Index(
            "uq_one_active_sub_per_user_product",
            "user_id", "product_id",
            unique=True,
            postgresql_where="is_active = true"
        ),
        # Fast lookups for expiry checks
        Index("ix_sub_active_end_date", "is_active", "end_date"),
        Index("ix_sub_user_active", "user_id", "is_active"),
    )
