from typing import Optional
from sqlalchemy.future import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.channel import TelegramChannel
from app.repositories.base import BaseRepository


class TelegramChannelRepository(BaseRepository[TelegramChannel]):
    def __init__(self, db: AsyncSession):
        super().__init__(TelegramChannel, db)

    async def get_by_telegram_id(self, telegram_id: str) -> Optional[TelegramChannel]:
        """Fetch channel by Telegram ID."""
        result = await self.db.execute(
            select(TelegramChannel).filter(TelegramChannel.telegram_id == telegram_id)
        )
        return result.scalars().first()
