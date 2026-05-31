from typing import List
from sqlalchemy.future import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.product import ProductPackage
from app.repositories.base import BaseRepository


class PackageRepository(BaseRepository[ProductPackage]):
    def __init__(self, db: AsyncSession):
        super().__init__(ProductPackage, db)

    async def list_active(self) -> List[ProductPackage]:
        """Fetch all active packages."""
        result = await self.db.execute(select(ProductPackage).filter(ProductPackage.is_active == True))
        return list(result.scalars().all())
