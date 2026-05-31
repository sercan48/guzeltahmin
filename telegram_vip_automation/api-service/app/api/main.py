from contextlib import asynccontextmanager
import logging

from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.core.config import settings
from app.core.middleware import RequestIdMiddleware
from app.db.session import engine
from app.models.base import Base
from app.models.package import Package
from app.api.routes import router as api_router
from app.api.metrics import router as metrics_router
from shared.logging import configure_structured_logging

# Configure structured JSON logging
configure_structured_logging(
    service="api-service",
    level="INFO" if settings.ENV == "production" else "DEBUG"
)
logger = logging.getLogger(__name__)


async def seed_packages():
    """Seeds default packages if the package table is empty."""
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


async def seed_admin():
    """Seeds default admin if the admin table is empty."""
    from app.services.admin import AdminService
    async with AsyncSession(engine) as session:
        try:
            async with session.begin():
                admin_service = AdminService(session)
                await admin_service.create_initial_admin()
                await session.commit()
        except Exception as e:
            logger.error(f"Error seeding admin: {e}")
            await session.rollback()


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Initializing database tables...")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("Database tables initialized.")

    await seed_packages()
    await seed_admin()

    yield
    logger.info("Shutting down API server...")


app = FastAPI(
    title="Telegram VIP Membership & Payment Automation API",
    description="Production-grade backend API for VIP subscription processing.",
    version="2.0.0",
    lifespan=lifespan,
    debug=settings.DEBUG
)

# Middleware stack
app.add_middleware(RequestIdMiddleware)

# Route registration
app.include_router(api_router, prefix="/api/v1")
app.include_router(metrics_router, prefix="/api/v1")


@app.get("/")
async def root():
    return {
        "app": "Telegram VIP Automation",
        "version": "2.0.0",
        "documentation": "/docs",
        "metrics": "/api/v1/metrics"
    }
