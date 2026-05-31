import logging
from datetime import datetime
from aiogram import Bot
from app.core.config import settings

logger = logging.getLogger(__name__)


class TelegramService:
    def __init__(self, bot: Optional[Bot] = None):
        # Allow passing an existing bot instance or initialize from settings
        self.bot = bot or Bot(token=settings.BOT_TOKEN)

    async def create_single_use_invite(self, chat_id: str, name: str, expire_date: Optional[datetime] = None) -> Optional[str]:
        """Create a single-use chat invite link with member limit 1."""
        try:
            expire_timestamp = int(expire_date.timestamp()) if expire_date else None
            
            invite = await self.bot.create_chat_invite_link(
                chat_id=chat_id,
                name=name,
                expire_date=expire_timestamp,
                member_limit=1,
                creates_join_request=False
            )
            logger.info(f"Created invite link for chat {chat_id}: {invite.invite_link}")
            return invite.invite_link
        except Exception as e:
            logger.error(f"Failed to create chat invite link for {chat_id}: {e}")
            return None

    async def kick_user(self, chat_id: str, user_id: int) -> bool:
        """Kick a user by banning and then unbanning them (to remove them without permanent block)."""
        try:
            # 1. Ban user
            await self.bot.ban_chat_member(chat_id=chat_id, user_id=user_id)
            # 2. Unban user (removes them from block list so they can rejoin in the future)
            await self.bot.unban_chat_member(chat_id=chat_id, user_id=user_id, only_if_banned=True)
            logger.info(f"Kicked and unbanned user {user_id} from chat {chat_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to kick user {user_id} from {chat_id}: {e}")
            return False

    async def close(self):
        """Close bot session if initialized internally."""
        await self.bot.session.close()


from typing import Optional
