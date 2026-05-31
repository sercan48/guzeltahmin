import asyncio
import logging
from aiogram import Bot
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from workers.config import settings
from workers.jobs.expire_checker import check_and_cleanup_expired_subscriptions
from workers.jobs.campaign_notification import send_automated_renewal_campaigns
from workers.jobs.churn_risk import run_churn_risk_calculator

logging.basicConfig(
    level=logging.INFO if settings.ENV == "production" else logging.DEBUG,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


async def main():
    logger.info("Starting Worker Service...")

    # Initialize Bot (used only for API requests, no polling needed here)
    bot = Bot(token=settings.BOT_TOKEN)

    # Setup Async Scheduler
    scheduler = AsyncIOScheduler()
    interval_minutes = 1 if settings.ENV == "development" else 60
    
    scheduler.add_job(
        check_and_cleanup_expired_subscriptions,
        "interval",
        minutes=interval_minutes,
        args=[bot],
        id="worker_membership_expiration_check",
        replace_existing=True
    )
    
    scheduler.add_job(
        send_automated_renewal_campaigns,
        "interval",
        minutes=interval_minutes,
        args=[bot],
        id="worker_renewal_campaigns_notification",
        replace_existing=True
    )

    # Risk calculation runs daily at 3 AM in production, or on interval in development
    risk_trigger_type = "interval" if settings.ENV == "development" else "cron"
    risk_trigger_args = {"minutes": interval_minutes} if settings.ENV == "development" else {"hour": 3}
    
    scheduler.add_job(
        run_churn_risk_calculator,
        risk_trigger_type,
        **risk_trigger_args,
        id="worker_churn_risk_calculation",
        replace_existing=True
    )
    
    scheduler.start()
    logger.info(f"Worker scheduler started. Jobs will run every {interval_minutes} minute(s).")

    try:
        # Keep the worker process alive
        while True:
            await asyncio.sleep(3600)
    except (KeyboardInterrupt, SystemExit):
        logger.info("Worker Service stopping...")
    finally:
        scheduler.shutdown()
        await bot.session.close()
        logger.info("Worker Service stopped.")


if __name__ == "__main__":
    asyncio.run(main())
