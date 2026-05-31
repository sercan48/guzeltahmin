from typing import Generic, TypeVar, Type, List, Optional, Any
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from app.models.base import Base

ModelType = TypeVar("ModelType", bound=Base)


class BaseRepository(Generic[ModelType]):
    """Generic base class for implementing Repository pattern."""

    def __init__(self, model: Type[ModelType], db: AsyncSession):
        self.model = model
        self.db = db

    async def get(self, id: Any) -> Optional[ModelType]:
        """Fetch database record by primary key id."""
        result = await self.db.execute(select(self.model).filter(self.model.id == id))
        return result.scalars().first()

    async def list(self) -> List[ModelType]:
        """Fetch all database records for this model."""
        result = await self.db.execute(select(self.model))
        return list(result.scalars().all())

    async def create(self, obj: ModelType) -> ModelType:
        """Add new record to database."""
        self.db.add(obj)
        await self.db.flush()
        return obj

    async def update(self, obj: ModelType) -> ModelType:
        """Commit updates to the record."""
        self.db.add(obj)
        await self.db.flush()
        return obj

    async def delete(self, id: Any) -> bool:
        """Delete record by id."""
        obj = await self.get(id)
        if obj:
            await self.db.delete(obj)
            await self.db.flush()
            return True
        return False
