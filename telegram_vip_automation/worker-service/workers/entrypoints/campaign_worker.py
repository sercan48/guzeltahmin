import asyncio
import logging
from aiogram import Bot
from workers.config import settings
from workers.jobs.campaign_notification import send_automated_renewal_campaigns
from workers.entrypoints.runner import run_job_with_retry

logging.basicConfig(
    level=logging.INFO if settings.ENV == "production" else logging.DEBUG,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("campaign_worker")


async def main():
    logger.info("Starting Campaign Worker process...")
    bot = Bot(token=settings.BOT_TOKEN)
    
    interval = 60 if settings.ENV == "production" else 30  # Run every 60s in prod, 30s in dev
    
    try:
        while True:
            await run_job_with_retry(
                send_automated_renewal_campaigns,
                job_name="campaign_check",
                lock_name="campaign_check_lock",
                bot=bot
            )
            await asyncio.sleep(interval)
    except asyncio.CancelledError:
        logger.info("Campaign Worker shutting down...")
    finally:
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
