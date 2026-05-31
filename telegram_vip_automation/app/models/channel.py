from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column, relationship
from typing import List, TYPE_CHECKING
from app.models.base import Base

if TYPE_CHECKING:
    from app.models.subscription import Subscription
    from app.models.invite import InviteLog


class TelegramChannel(Base):
    __tablename__ = "telegram_channels"

    telegram_id: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    invite_link: Mapped[str] = mapped_column(String(512), nullable=True)

    # Relationships
    subscriptions: Mapped[List["Subscription"]] = relationship(back_populates="channel")
    invite_logs: Mapped[List["InviteLog"]] = relationship(back_populates="channel")
