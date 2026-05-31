import logging
from datetime import datetime
from aiogram import Bot
from sqlalchemy.future import select

from app.db.session import async_session
from app.repositories.subscription import SubscriptionRepository
from app.repositories.user import UserRepository
from app.services.telegram import TelegramService
from app.core.config import settings

logger = logging.getLogger(__name__)


async def check_and_cleanup_expired_subscriptions(bot: Bot):
    """
    Checks the database for expired active subscriptions,
    removes the user from the VIP channel, and marks their subscription inactive.
    """
    logger.info("Starting expiration check...")
    
    async with async_session() as db:
        sub_repo = SubscriptionRepository(db)
        user_repo = UserRepository(db)
        tg_service = TelegramService(bot=bot)
        
        try:
            # 1. Fetch expired subscriptions
            expired_subs = await sub_repo.list_expired_active_subscriptions()
            
            if not expired_subs:
                logger.info("No expired subscriptions found.")
                return
                
            logger.info(f"Found {len(expired_subs)} expired subscriptions to process.")
            
            for sub in expired_subs:
                # 2. Fetch User to get Telegram ID
                user = await user_repo.get(sub.user_id)
                if not user:
                    logger.error(f"User not found for subscription ID {sub.id}, user_id {sub.user_id}")
                    continue
                    
                logger.info(f"Processing expiration for user {user.telegram_id} (Sub ID: {sub.id})")
                
                # 3. Kick user from the VIP Channel
                kick_success = await tg_service.kick_user(
                    chat_id=settings.VIP_CHANNEL_ID,
                    user_id=user.telegram_id
                )
                
                # 4. Update database state
                sub.is_active = False
                await sub_repo.update(sub)
                
                # 5. Notify user via Telegram Bot direct message
                if kick_success:
                    try:
                        await bot.send_message(
                            chat_id=user.telegram_id,
                            text=(
                                "⚠️ **VIP Abonelik Süreniz Sona Erdi!**\n\n"
                                "VIP kanalına erişim süreniz dolduğu için otomatik olarak gruptan çıkarıldınız. "
                                "Tekrar katılmak ve tahminlerimizi kaçırmamak için yeni bir paket satın alabilirsiniz.\n\n"
                                "👉 /start komutunu göndererek menüyü açabilirsiniz."
                            ),
                            parse_mode="Markdown"
                        )
                        logger.info(f"Sent expiration notification to user {user.telegram_id}")
                    except Exception as bot_err:
                        logger.warning(f"Could not send DM to user {user.telegram_id}: {bot_err}")
                else:
                    logger.error(f"Failed to kick user {user.telegram_id} from VIP Channel. Database state still marked inactive.")
            
            await db.commit()
            logger.info("Expiration check finished successfully.")
            
        except Exception as e:
            logger.error(f"Error during subscription expiration check: {e}")
            await db.rollback()
