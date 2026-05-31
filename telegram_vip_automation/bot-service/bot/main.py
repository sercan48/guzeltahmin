import asyncio
import logging
from aiogram import Bot, Dispatcher
from bot.config import settings
from bot.handlers.handlers import router as bot_router

logging.basicConfig(
    level=logging.INFO if settings.ENV == "production" else logging.DEBUG,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


async def main():
    logger.info("Starting Bot Service...")
    
    # Initialize Bot & Dispatcher
    bot = Bot(token=settings.BOT_TOKEN)
    dp = Dispatcher()
    dp.include_router(bot_router)

    try:
        await dp.start_polling(bot)
    except (KeyboardInterrupt, SystemExit):
        logger.info("Bot Service stopped manually.")
    finally:
        await bot.session.close()
        logger.info("Bot Service session closed.")


if __name__ == "__main__":
    asyncio.run(main())
