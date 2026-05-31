from sqlalchemy import String, ForeignKey, Text
from sqlalchemy.orm import Mapped, mapped_column
from typing import Optional
from app.models.base import Base


class AdminAuditLog(Base):
    __tablename__ = "admin_audit_logs"

    admin_id: Mapped[int] = mapped_column(ForeignKey("admins.id", ondelete="CASCADE"), nullable=False)
    action: Mapped[str] = mapped_column(String(100), nullable=False)  # e.g. suspend_user, grant_days
    target_type: Mapped[str] = mapped_column(String(50), nullable=False)  # user, subscription, coupon, etc.
    target_id: Mapped[str] = mapped_column(String(100), nullable=False)
    details: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    ip_address: Mapped[Optional[str]] = mapped_column(String(45), nullable=True)
