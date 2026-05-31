import asyncio
import logging
from workers.config import settings
from workers.client.api_client import worker_api_client
from workers.entrypoints.runner import run_job_with_retry

logging.basicConfig(
    level=logging.INFO if settings.ENV == "production" else logging.DEBUG,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("analytics_worker")


async def refresh_views_job():
    """Trigger materialized view refresh in API service."""
    success = await worker_api_client.trigger_materialized_view_refresh()
    if not success:
        raise RuntimeError("Failed to refresh materialized views via API Service.")


async def main():
    logger.info("Starting Analytics Worker process...")
    
    # Run once an hour. For dev, we run every 5 minutes (300s).
    interval = 3600 if settings.ENV == "production" else 300
    
    try:
        while True:
            await run_job_with_retry(
                refresh_views_job,
                job_name="analytics_mv_refresh",
                lock_name="analytics_refresh_lock"
            )
            await asyncio.sleep(interval)
    except asyncio.CancelledError:
        logger.info("Analytics Worker shutting down...")


if __name__ == "__main__":
    asyncio.run(main())
