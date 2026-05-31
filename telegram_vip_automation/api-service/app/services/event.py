import logging
import uuid
import json
import random
from typing import Optional, Any
import redis.asyncio as aioredis
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.event_log import EventLog
from app.core.config import settings

logger = logging.getLogger(__name__)

_redis_client: Optional[aioredis.Redis] = None


async def _get_redis() -> Optional[aioredis.Redis]:
    global _redis_client
    if _redis_client is not None:
        return _redis_client
    if not settings.REDIS_URL:
        return None
    try:
        _redis_client = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
        await _redis_client.ping()
        return _redis_client
    except Exception as exc:
        logger.warning("Redis unavailable for Event Pipeline: %s", exc)
        _redis_client = None
        return None


class EventService:
    """Service to handle centralized event logging and triggers."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def log_event(
        self,
        event_type: str,
        user_id: Optional[int] = None,
        payload_json: Optional[dict[str, Any]] = None,
        correlation_id: Optional[str] = None,
        source_service: str = "api"
    ) -> EventLog:
        """Centralized event publisher. Pushes to Redis Stream or falls back to direct DB write on failure."""
        cid = correlation_id or f"corr_{uuid.uuid4().hex}"
        payload = payload_json or {}

        # Modül 7: Event Sampling (Low priority events sampled at 10%)
        LOW_PRIORITY_EVENTS = {"bot_interaction", "menu_clicked", "page_viewed", "user_active_check"}
        if event_type in LOW_PRIORITY_EVENTS:
            if random.random() > 0.10:
                logger.debug(f"Event {event_type} sampled out.")
                return EventLog(
                    event_type=event_type,
                    user_id=user_id,
                    payload_json=payload,
                    correlation_id=cid,
                    source_service=source_service
                )

        redis = await _get_redis()
        if redis:
            try:
                msg_data = {
                    "event_type": event_type,
                    "user_id": str(user_id) if user_id is not None else "",
                    "payload_json": json.dumps(payload),
                    "correlation_id": cid,
                    "source_service": source_service
                }
                msg_id = await redis.xadd("event_stream", msg_data, maxlen=100000, approximate=True)
                
                logger.info(
                    f"Event pushed to Redis Stream: {event_type} (Correlation: {cid}, MsgID: {msg_id})"
                )
                
                return EventLog(
                    event_type=event_type,
                    user_id=user_id,
                    payload_json=payload,
                    correlation_id=cid,
                    source_service=source_service,
                    redis_msg_id=msg_id
                )
            except Exception as e:
                logger.warning(f"Failed to push to Redis stream, falling back to direct DB write: {e}")

        # Fallback to direct DB write
        event = EventLog(
            event_type=event_type,
            user_id=user_id,
            payload_json=payload,
            correlation_id=cid,
            source_service=source_service
        )
        self.db.add(event)
        await self.db.flush()

        logger.info(
            f"Event logged directly to DB (Fallback): {event_type} (Correlation: {cid}, Source: {source_service})"
        )

        try:
            from app.services.campaign import CampaignService
            campaign_service = CampaignService(self.db)
            await campaign_service.trigger_campaigns_on_event(event)
        except Exception as e:
            logger.error(f"Failed to evaluate campaign rules for event {event_type}: {e}")

        return event

