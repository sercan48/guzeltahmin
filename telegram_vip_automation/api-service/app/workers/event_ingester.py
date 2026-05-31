"""Event Ingest worker daemon process.

Consumes events from Redis Stream 'event_stream' and persists them to the PostgreSQL database.
"""

import asyncio
import json
import logging
import time
import os
import uuid
from typing import Optional

import redis.asyncio as aioredis
from app.core.config import settings

logging.basicConfig(
    level=logging.INFO if settings.ENV == "production" else logging.DEBUG,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("event_ingester")


async def init_consumer_group(redis_client: aioredis.Redis):
    """Create the consumer group if it doesn't already exist."""
    try:
        await redis_client.xgroup_create(
            "event_stream", "event_ingestion_group", id="$", mkstream=True
        )
        logger.info("Created consumer group 'event_ingestion_group' on 'event_stream'.")
    except Exception as e:
        if "BUSYGROUP" in str(e):
            logger.debug("Consumer group 'event_ingestion_group' already exists.")
        else:
            logger.error(f"Error creating consumer group: {e}")


async def process_event(redis_client: aioredis.Redis, msg_id: str, fields: dict) -> bool:
    """Process a single event by persisting it to Postgres and triggering campaign rules."""
    event_type = fields.get("event_type")
    user_id_str = fields.get("user_id")
    user_id = int(user_id_str) if user_id_str else None
    payload_json = fields.get("payload_json", "{}")
    correlation_id = fields.get("correlation_id", f"corr_{uuid.uuid4().hex}")
    source_service = fields.get("source_service", "unknown")

    try:
        payload = json.loads(payload_json)
    except Exception:
        payload = {}

    from app.db.session import async_session
    from app.models.event_log import EventLog
    from app.services.campaign import CampaignService
    from sqlalchemy.exc import IntegrityError
    from sqlalchemy.future import select

    # Observability metric: Track start time
    start_time = time.time()

    async with async_session() as session:
        try:
            # 1. Idempotency Check: Verify if this redis_msg_id already exists in Postgres
            dup_check = await session.execute(
                select(EventLog).filter(EventLog.redis_msg_id == msg_id)
            )
            if dup_check.scalars().first():
                logger.info(f"Duplicate event ignored (Already in DB): MsgID {msg_id}")
                await redis_client.xack("event_stream", "event_ingestion_group", msg_id)
                return True

            # 2. Persist to PostgreSQL event_logs table
            event = EventLog(
                event_type=event_type,
                user_id=user_id,
                payload_json=payload,
                correlation_id=correlation_id,
                source_service=source_service,
                redis_msg_id=msg_id
            )
            session.add(event)
            await session.commit()
            await session.refresh(event)

            # 3. Trigger Campaigns synchronously within worker context
            try:
                # Open separate session to avoid session state mixing
                async with async_session() as camp_session:
                    async with camp_session.begin():
                        campaign_service = CampaignService(camp_session)
                        merged_event = await camp_session.merge(event)
                        await campaign_service.trigger_campaigns_on_event(merged_event)
            except Exception as e_camp:
                logger.error(f"Failed to evaluate campaign rules for event {event_type}: {e_camp}")

            # 4. Acknowledge message in Redis stream
            await redis_client.xack("event_stream", "event_ingestion_group", msg_id)
            
            # Track latency metric
            latency = time.time() - start_time
            await redis_client.set(f"metrics:latency:event_processing", str(latency))
            await redis_client.incr("metrics:counter:worker_job_success_rate:event_ingester")
            await redis_client.hincrby("metrics:hash:event_counts", event_type, 1)

            logger.info(f"Processed event: {event_type} (Correlation: {correlation_id}, MsgID: {msg_id})")

            return True
        except IntegrityError:
            await session.rollback()
            logger.info(f"Idempotency database insert collision ignored: MsgID {msg_id}")
            await redis_client.xack("event_stream", "event_ingestion_group", msg_id)
            return True
        except Exception as e:
            await session.rollback()
            logger.error(f"Error persisting event {msg_id} to DB: {e}")
            await redis_client.incr("metrics:counter:worker_retry_count:event_ingester")
            return False


async def recover_pending_events(redis_client: aioredis.Redis, consumer_name: str):
    """Recover and claim pending messages (PEL) that timed out (older than 60s)."""
    try:
        # Get first 50 pending messages
        pending = await redis_client.xpending_range(
            "event_stream", "event_ingestion_group", "-", "+", 50
        )
        for p in pending:
            msg_id = p.get("message_id")
            consumer = p.get("consumer")
            elapsed_ms = p.get("elapsed_time")
            delivery_count = p.get("times_delivered")

            # Check if message has been pending for > 60s
            if elapsed_ms > 60000:
                logger.info(
                    f"Reclaiming pending message {msg_id} from consumer {consumer} "
                    f"(elapsed: {elapsed_ms/1000}s, attempts: {delivery_count})"
                )

                # Move to DLQ after 5 failed attempts
                if delivery_count > 5:
                    logger.error(f"Message {msg_id} exceeded maximum retry attempts. Pushing to DLQ!")
                    
                    # Fetch message content from stream
                    messages = await redis_client.xread(streams={"event_stream": msg_id}, count=1)
                    if messages:
                        _, msg_list = messages[0]
                        _, msg_fields = msg_list[0]
                        dlq_payload = {
                            "message_id": msg_id,
                            "fields": msg_fields,
                            "error": f"Failed {delivery_count} delivery attempts in Event Ingester."
                        }
                        await redis_client.lpush("dlq:worker_jobs", json.dumps(dlq_payload))
                        
                    # Acknowledge the message to stop further attempts
                    await redis_client.xack("event_stream", "event_ingestion_group", msg_id)
                    logger.warning(f"DLQ: Message {msg_id} acknowledged and removed from active processing pipeline.")
                    continue

                # Claim the message
                claimed = await redis_client.xclaim(
                    "event_stream", "event_ingestion_group", consumer_name, 60000, [msg_id]
                )
                for cid, fields in claimed:
                    await process_event(redis_client, cid, fields)

    except Exception as e:
        logger.error(f"Error in pending messages recovery loop: {e}")


async def update_queue_lag_metric(redis_client: aioredis.Redis):
    """Compute event queue lag and save to Redis for Prometheus scrape."""
    try:
        pending_info = await redis_client.xpending("event_stream", "event_ingestion_group")
        min_id = pending_info.get("min")
        if min_id:
            msg_time_ms = int(min_id.split("-")[0])
            now_ms = int(time.time() * 1000)
            lag_sec = max(0.0, (now_ms - msg_time_ms) / 1000.0)
            await redis_client.set("metrics:event_queue_lag", str(lag_sec))
        else:
            await redis_client.set("metrics:event_queue_lag", "0.0")
    except Exception as e:
        logger.error(f"Error updating queue lag metrics: {e}")


async def main():
    logger.info("Starting Event Ingestion Worker...")
    
    if not settings.REDIS_URL:
        logger.error("REDIS_URL not configured. Exiting.")
        return

    # Initialize client
    redis_client = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
    await init_consumer_group(redis_client)
    
    consumer_name = f"event_ingester_{uuid.uuid4().hex[:8]}"
    logger.info(f"Consumer identity: {consumer_name}")

    # Set up loops
    loop_counter = 0

    try:
        while True:
            # 1. Fetch new messages from stream (BLOCK up to 2000ms)
            try:
                streams = await redis_client.xreadgroup(
                    groupname="event_ingestion_group",
                    consumername=consumer_name,
                    streams={"event_stream": ">"},
                    count=10,
                    block=2000
                )
            except Exception as read_err:
                logger.error(f"Error reading from stream: {read_err}")
                await asyncio.sleep(2)
                continue

            if streams:
                for stream_name, messages in streams:
                    for msg_id, fields in messages:
                        await process_event(redis_client, msg_id, fields)

            # 2. Run recovery and lag metric update periodically
            loop_counter += 1
            if loop_counter % 30 == 0:  # ~ Every 60 seconds
                await recover_pending_events(redis_client, consumer_name)
                await update_queue_lag_metric(redis_client)
                loop_counter = 0

    except asyncio.CancelledError:
        logger.info("Event Ingestion Worker shutting down gracefully...")
    finally:
        await redis_client.aclose()
        logger.info("Event Ingestion Worker stopped.")


if __name__ == "__main__":
    asyncio.run(main())
