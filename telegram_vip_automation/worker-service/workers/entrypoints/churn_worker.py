import asyncio
import logging
from workers.config import settings
from workers.jobs.churn_risk import run_churn_risk_calculator
from workers.entrypoints.runner import run_job_with_retry

logging.basicConfig(
    level=logging.INFO if settings.ENV == "production" else logging.DEBUG,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("churn_worker")


async def main():
    logger.info("Starting Churn Worker process...")
    
    # Run once a day at 3 AM. For dev, we run every 5 minutes.
    interval = 86400 if settings.ENV == "production" else 300
    
    try:
        while True:
            await run_job_with_retry(
                run_churn_risk_calculator,
                job_name="churn_risk_check",
                lock_name="churn_risk_check_lock"
            )
            await asyncio.sleep(interval)
    except asyncio.CancelledError:
        logger.info("Churn Worker shutting down...")


if __name__ == "__main__":
    asyncio.run(main())
