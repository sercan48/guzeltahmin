import asyncio
import logging
import uvicorn
from aiogram import Bot, Dispatcher
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.core.config import settings
from app.api.main import app as fastapi_app
from app.bot.handlers import router as bot_router
from app.schedulers.expire_checker import check_and_cleanup_expired_subscriptions

# Set up logging
logging.basicConfig(
    level=logging.INFO if settings.ENV == "production" else logging.DEBUG,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


async def run_fastapi():
    """Runs the FastAPI server using Uvicorn."""
    config = uvicorn.Config(
        app=fastapi_app,
        host="0.0.0.0",
        port=settings.PORT,
        log_level="info" if settings.ENV == "production" else "debug"
    )
    server = uvicorn.Server(config)
    await server.serve()


async def main():
    logger.info("Starting Telegram VIP Automation System...")

    # 1. Initialize Telegram Bot & Dispatcher
    bot = Bot(token=settings.BOT_TOKEN)
    dp = Dispatcher()
    dp.include_router(bot_router)

    # 2. Setup Scheduler for Expired Subscriptions
    scheduler = AsyncIOScheduler()
    # Runs the check every minute in development for testing, or hourly
    interval_minutes = 1 if settings.ENV == "development" else 60
    scheduler.add_job(
        check_and_cleanup_expired_subscriptions,
        "interval",
        minutes=interval_minutes,
        args=[bot],
        id="membership_expiration_check",
        replace_existing=True
    )
    scheduler.start()
    logger.info(f"Subscription expiration check scheduled every {interval_minutes} minute(s).")

    # 3. Run FastAPI server and Telegram Bot concurrently
    try:
        await asyncio.gather(
            run_fastapi(),
            dp.start_polling(bot)
        )
    except (KeyboardInterrupt, SystemExit):
        logger.info("Application stopped manually.")
    finally:
        # Cleanup
        scheduler.shutdown()
        await bot.session.close()
        logger.info("Bot session and scheduler closed.")


if __name__ == "__main__":
    asyncio.run(main())
