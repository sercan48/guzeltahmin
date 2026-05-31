import logging
import asyncio
from aiogram import Bot
from workers.client.api_client import worker_api_client
from workers.config import settings
from shared.redis_lock import DistributedLock, LockAcquisitionError

logger = logging.getLogger(__name__)


async def check_and_cleanup_expired_subscriptions(bot: Bot):
    """
    Worker Job:
    1. Acquires a Redis distributed lock to prevent concurrent runs across instances.
    2. Fetches expired subscriptions via API Service.
    3. Removes the users from the VIP channel using Telegram API.
    4. Deactivates the subscriptions in the database via API Service.
    """
    logger.info("Attempting background expiration check...")
    
    try:
        async with DistributedLock(settings.REDIS_URL, "expire_check") as lock:
            logger.info("Lock 'expire_check' acquired. Starting check...")
            
            # 1. Fetch expired subscriptions from API Service
            try:
                expired_subs = await worker_api_client.get_expired_subscriptions()
            except Exception as exc:
                logger.error(f"Failed to fetch expired subscriptions: {exc}")
                return

            if not expired_subs:
                logger.info("No expired subscriptions found.")
                return
                
            logger.info(f"Found {len(expired_subs)} expired subscriptions to process.")
            
            for index, sub in enumerate(expired_subs):
                sub_id = sub.get("subscription_id")
                telegram_id = sub.get("telegram_id")
                user_id = sub.get("user_id")
                channels = sub.get("channels", [settings.VIP_CHANNEL_ID])
                
                if not sub_id or not telegram_id:
                    continue
                    
                logger.info(f"Processing expiration for user {telegram_id} (Sub ID: {sub_id})")
                
                # 2. Kick user from all channels linked to the subscription's product
                kicked_channels = []
                for channel_id in channels:
                    try:
                        # Ban user to remove them
                        await bot.ban_chat_member(chat_id=channel_id, user_id=telegram_id)
                        # Unban user to remove them from block list
                        await bot.unban_chat_member(chat_id=channel_id, user_id=telegram_id, only_if_banned=True)
                        logger.info(f"Kicked and unbanned user {telegram_id} from chat {channel_id}")
                        kicked_channels.append(channel_id)
                        
                        # Emit event: user_removed_from_channel
                        try:
                            await worker_api_client.publish_event(
                                event_type="user_removed_from_channel",
                                user_id=user_id,
                                payload_json={"channel_id": channel_id}
                            )
                        except Exception as e_event:
                            logger.error(f"Failed to publish user_removed_from_channel event: {e_event}")
                    except Exception as e:
                        logger.error(f"Failed to kick user {telegram_id} from {channel_id}: {e}")
                
                kick_success = len(kicked_channels) > 0
                
                # 3. Update database state via API Service
                deactivate_success = False
                try:
                    deactivate_success = await worker_api_client.deactivate_subscription(sub_id)
                    if deactivate_success:
                        # Emit event: subscription_expired
                        try:
                            await worker_api_client.publish_event(
                                event_type="subscription_expired",
                                user_id=user_id,
                                payload_json={"subscription_id": sub_id}
                            )
                        except Exception as e_event:
                            logger.error(f"Failed to publish subscription_expired event: {e_event}")
                    else:
                        logger.error(f"Failed to update subscription status {sub_id} in API Service.")
                except Exception as e:
                    logger.error(f"Error calling deactivation endpoint for subscription {sub_id}: {e}")
                
                # 4. Notify user via Telegram Bot DM if kick succeeded
                if kick_success:
                    try:
                        await bot.send_message(
                            chat_id=telegram_id,
                            text=(
                                "⚠️ **VIP Abonelik Süreniz Sona Erdi!**\n\n"
                                "VIP kanalına erişim süreniz dolduğu için otomatik olarak gruptan çıkarıldınız. "
                                "Tekrar katılmak ve tahminlerimizi kaçırmamak için yeni bir paket satın alabilirsiniz.\n\n"
                                "👉 /start komutunu göndererek menüyü açabilirsiniz."
                            ),
                            parse_mode="Markdown"
                        )
                        logger.info(f"Sent expiration notification to user {telegram_id}")
                    except Exception as bot_err:
                        logger.warning(f"Could not send DM to user {telegram_id}: {bot_err}")
                
                # Rate limit kicks to protect against Telegram rate limiting (2 seconds between each kick)
                if index < len(expired_subs) - 1:
                    logger.debug("Sleeping 2 seconds before processing next user to satisfy rate limit...")
                    await asyncio.sleep(2)
                    
            logger.info("Expiration check job run completed.")
            
    except LockAcquisitionError:
        logger.warning("Could not acquire distributed lock 'expire_check'. Another worker instance is running. Skipping this run.")
    except Exception as e:
        logger.error(f"Unexpected error during subscription expiration check worker job: {e}")
