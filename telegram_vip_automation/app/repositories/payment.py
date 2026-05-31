from typing import Optional
from sqlalchemy.future import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.payment import Payment
from app.repositories.base import BaseRepository


class PaymentRepository(BaseRepository[Payment]):
    def __init__(self, db: AsyncSession):
        super().__init__(Payment, db)

    async def get_by_provider_tx_id(self, tx_id: str) -> Optional[Payment]:
        """Fetch payment record by provider transaction ID."""
        result = await self.db.execute(select(Payment).filter(Payment.provider_tx_id == tx_id))
        return result.scalars().first()
