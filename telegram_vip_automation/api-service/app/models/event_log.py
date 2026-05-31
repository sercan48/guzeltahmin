from sqlalchemy import String, ForeignKey, JSON
from sqlalchemy.orm import Mapped, mapped_column
from typing import Optional
from app.models.base import Base


class EventLog(Base):
    __tablename__ = "event_logs"

    event_type: Mapped[str] = mapped_column(String(100), index=True, nullable=False)
    user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    payload_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    correlation_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    source_service: Mapped[str] = mapped_column(String(50), nullable=False)  # api, bot, worker
    redis_msg_id: Mapped[Optional[str]] = mapped_column(String(64), unique=True, nullable=True)

