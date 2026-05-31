from typing import List, Optional
from datetime import datetime
from sqlalchemy.future import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.subscription import Subscription
from app.repositories.base import BaseRepository


class SubscriptionRepository(BaseRepository[Subscription]):
    def __init__(self, db: AsyncSession):
        super().__init__(Subscription, db)

    async def get_active_by_user(self, user_id: int) -> Optional[Subscription]:
        """Fetch the active subscription of a user."""
        result = await self.db.execute(
            select(Subscription)
            .filter(Subscription.user_id == user_id, Subscription.is_active == True)
        )
        return result.scalars().first()

    async def list_by_user(self, user_id: int) -> List[Subscription]:
        """Fetch all subscriptions of a user."""
        result = await self.db.execute(
            select(Subscription)
            .filter(Subscription.user_id == user_id)
            .order_by(Subscription.created_at.desc())
        )
        return list(result.scalars().all())

    async def list_expired_active_subscriptions(self) -> List[Subscription]:
        """List subscriptions that are active but their end_date is in the past."""
        now = datetime.now()
        result = await self.db.execute(
            select(Subscription)
            .filter(Subscription.is_active == True, Subscription.end_date < now)
        )
        return list(result.scalars().all())
