from sqlalchemy import ForeignKey, String, Float, Integer, Boolean
from sqlalchemy.orm import Mapped, mapped_column, relationship
from typing import List, Optional, TYPE_CHECKING
from app.models.base import Base

if TYPE_CHECKING:
    from app.models.subscription import Subscription
    from app.models.payment import Payment
    from app.models.channel import TelegramChannel


class Product(Base):
    __tablename__ = "products"

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # Relationships
    packages: Mapped[List["ProductPackage"]] = relationship(back_populates="product", cascade="all, delete-orphan")
    channels: Mapped[List["ProductChannel"]] = relationship(back_populates="product", cascade="all, delete-orphan")
    subscriptions: Mapped[List["Subscription"]] = relationship(back_populates="product", cascade="all, delete-orphan")


class ProductChannel(Base):
    __tablename__ = "product_channels"

    product_id: Mapped[int] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"), nullable=False)
    channel_id: Mapped[int] = mapped_column(ForeignKey("telegram_channels.id", ondelete="CASCADE"), nullable=False)

    # Relationships
    product: Mapped["Product"] = relationship(back_populates="channels")
    channel: Mapped["TelegramChannel"] = relationship()


class ProductPackage(Base):
    __tablename__ = "product_packages"

    product_id: Mapped[int] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)
    price: Mapped[float] = mapped_column(Float, nullable=False)
    duration_days: Mapped[int] = mapped_column(Integer, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # Relationships
    product: Mapped["Product"] = relationship(back_populates="packages")
    subscriptions: Mapped[List["Subscription"]] = relationship(back_populates="package")
    payments: Mapped[List["Payment"]] = relationship(back_populates="package")
