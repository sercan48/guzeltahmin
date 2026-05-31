from sqlalchemy import ForeignKey, Integer, String, JSON
from sqlalchemy.orm import Mapped, mapped_column
from app.models.base import Base


class UserRiskScore(Base):
    __tablename__ = "user_risk_scores"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False)
    risk_score: Mapped[int] = mapped_column(Integer, default=0, nullable=False)  # 0-100
    risk_segment: Mapped[str] = mapped_column(String(20), default="LOW", nullable=False)  # LOW, MEDIUM, HIGH, CRITICAL
    signals_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
