from sqlalchemy import ForeignKey, String, Index
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.models.base import Base


class NotificationLog(Base):
    __tablename__ = "notification_logs"

    subscription_id: Mapped[int] = mapped_column(ForeignKey("subscriptions.id", ondelete="CASCADE"), nullable=False)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    notification_type: Mapped[str] = mapped_column(String(50), nullable=False)  # T-7, T-3, T-1, T+1, T+7
    status: Mapped[str] = mapped_column(String(50), default="sent", nullable=False)

    # Relationships
    subscription = relationship("Subscription")
    user = relationship("User")

    __table_args__ = (
        Index("ix_notification_sub_type", "subscription_id", "notification_type"),
    )
