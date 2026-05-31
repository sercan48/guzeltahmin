import asyncio
import logging
from workers.config import settings
from workers.client.api_client import worker_api_client
from workers.entrypoints.runner import run_job_with_retry

logging.basicConfig(
    level=logging.INFO if settings.ENV == "production" else logging.DEBUG,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("reconciliation_worker")


async def reconciliation_job():
    """Trigger daily reconciliation check in API service."""
    res = await worker_api_client.trigger_reconciliation()
    if not res or res.get("status") != "success":
        raise RuntimeError(f"Reconciliation failed via API: {res}")
    logger.info(f"Reconciliation completed successfully: {res.get('stats')}")


async def main():
    logger.info("Starting Reconciliation Worker process...")
    
    # Run once a day. For dev, we run every 5 minutes (300s).
    interval = 86400 if settings.ENV == "production" else 300
    
    try:
        while True:
            await run_job_with_retry(
                reconciliation_job,
                job_name="reconciliation_check",
                lock_name="reconciliation_lock"
            )
            await asyncio.sleep(interval)
    except asyncio.CancelledError:
        logger.info("Reconciliation Worker shutting down...")


if __name__ == "__main__":
    asyncio.run(main())
