from typing import List
from sqlalchemy.future import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.package import Package
from app.repositories.base import BaseRepository


class PackageRepository(BaseRepository[Package]):
    def __init__(self, db: AsyncSession):
        super().__init__(Package, db)

    async def list_active(self) -> List[Package]:
        """Fetch all active packages."""
        result = await self.db.execute(select(Package).filter(Package.is_active == True))
        return list(result.scalars().all())
