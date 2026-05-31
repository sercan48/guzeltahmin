import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from app.core.config import settings
from app.db.session import engine
from app.models.base import Base
from app.models.package import Package
from app.api.routes import router as api_router
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

# Configure logging
logging.basicConfig(
    level=logging.INFO if settings.ENV == "production" else logging.DEBUG,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


async def seed_packages():
    """Seeds default packages if the package table is empty."""
    async_session_maker = engine.scalars if hasattr(engine, "scalars") else None
    # Use direct connection session
    async with AsyncSession(engine) as session:
        try:
            async with session.begin():
                result = await session.execute(select(Package))
                packages = result.scalars().all()
                if not packages:
                    logger.info("Seeding default packages...")
                    default_packages = [
                        Package(
                            name="1 Aylık VIP",
                            description="1 Ay boyunca VIP tahminlere ve analizlere tam erişim.",
                            price=299.90,
                            duration_days=30,
                            is_active=True
                        ),
                        Package(
                            name="3 Aylık VIP",
                            description="3 Ay boyunca VIP tahminlere ve analizlere tam erişim (%15 İndirimli).",
                            price=749.90,
                            duration_days=90,
                            is_active=True
                        ),
                        Package(
                            name="6 Aylık VIP",
                            description="6 Ay boyunca VIP tahminlere ve analizlere tam erişim (%30 İndirimli).",
                            price=1249.90,
                            duration_days=180,
                            is_active=True
                        )
                    ]
                    session.add_all(default_packages)
                    await session.commit()
                    logger.info("Seeding completed successfully.")
                else:
                    logger.info("Packages already exist, skipping seed.")
        except Exception as e:
            logger.error(f"Error seeding default packages: {e}")
            await session.rollback()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: Initialize DB tables
    logger.info("Initializing database tables...")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("Database tables initialized.")
    
    # Seed default packages
    await seed_packages()
    
    yield
    # Shutdown: Clean up resources if necessary
    logger.info("Shutting down API server...")


app = FastAPI(
    title="Telegram VIP Membership & Payment Automation API",
    description="Backend API for VIP subscription processing, payment confirmation, and automated tracking.",
    version="1.0.0",
    lifespan=lifespan,
    debug=settings.DEBUG
)

# Include routes
app.include_router(api_router, prefix="/api/v1")


@app.get("/")
async def root():
    return {
        "app": "Telegram VIP Automation",
        "version": "1.0.0",
        "documentation": "/docs"
    }
