from typing import List, Optional
from sqlalchemy.future import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.invite import InviteLog
from app.repositories.base import BaseRepository


class InviteLogRepository(BaseRepository[InviteLog]):
    def __init__(self, db: AsyncSession):
        super().__init__(InviteLog, db)

    async def get_by_invite_link(self, link: str) -> Optional[InviteLog]:
        """Fetch invite log by invite link."""
        result = await self.db.execute(select(InviteLog).filter(InviteLog.invite_link == link))
        return result.scalars().first()

    async def list_unused_by_user(self, user_id: int) -> List[InviteLog]:
        """Fetch all unused invite links generated for a user."""
        result = await self.db.execute(
            select(InviteLog)
            .filter(InviteLog.user_id == user_id, InviteLog.is_used == False)
        )
        return list(result.scalars().all())
