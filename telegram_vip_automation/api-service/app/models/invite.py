from datetime import datetime
from sqlalchemy import DateTime, ForeignKey, Boolean, String, Integer, Index, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from typing import Optional, TYPE_CHECKING
from app.models.base import Base

if TYPE_CHECKING:
    from app.models.user import User
    from app.models.channel import TelegramChannel


class InviteLog(Base):
    __tablename__ = "invite_logs"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    channel_id: Mapped[int] = mapped_column(ForeignKey("telegram_channels.id"), nullable=False)
    payment_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    invite_link: Mapped[str] = mapped_column(String(512), unique=True, nullable=False)

    is_used: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    used_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    expire_date: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    # Relationships
    user: Mapped["User"] = relationship(back_populates="invite_logs")
    channel: Mapped["TelegramChannel"] = relationship(back_populates="invite_logs")

    __table_args__ = (
        # Each invite link must be globally unique
        UniqueConstraint("invite_link", name="uq_invite_link"),
        # Fast lookup by payment
        Index("ix_invite_payment", "payment_id"),
        # Unused invites lookup per user
        Index("ix_invite_user_unused", "user_id", "is_used"),
    )
