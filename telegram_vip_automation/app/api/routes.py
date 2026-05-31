from fastapi import APIRouter, Depends, HTTPException, Header
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from typing import List, Dict, Any, Optional

from app.db.session import get_db
from app.models.package import Package
from app.repositories.user import UserRepository
from app.repositories.subscription import SubscriptionRepository
from app.services.telegram import TelegramService
from app.services.payment import PaymentService

router = APIRouter()


class MockWebhookPayload:
    pass # Simple wrapper schema is defined below


@router.get("/health")
async def health_check(db: AsyncSession = Depends(get_db)):
    """Health check endpoint checking DB connectivity."""
    try:
        # Execute simple query to test DB
        await db.execute(select(1))
        return {"status": "healthy", "database": "connected"}
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Database connection failed: {str(e)}"
        )


@router.get("/packages")
async def get_packages(db: AsyncSession = Depends(get_db)):
    """Fetch all active packages available for membership."""
    result = await db.execute(select(Package).filter(Package.is_active == True))
    packages = result.scalars().all()
    return [
        {
            "id": p.id,
            "name": p.name,
            "description": p.description,
            "price": p.price,
            "duration_days": p.duration_days
        }
        for p in packages
    ]


from pydantic import BaseModel

class WebhookPayload(BaseModel):
    provider_tx_id: str


@router.post("/payments/webhook/mock")
async def mock_payment_webhook(payload: WebhookPayload, db: AsyncSession = Depends(get_db)):
    """Mock webhook to simulate payment confirmation from gateways."""
    # Initialize services
    tg_service = TelegramService()
    pay_service = PaymentService(db, tg_service)
    
    try:
        payment, subscription, invite_link = await pay_service.confirm_payment(
            provider_tx_id=payload.provider_tx_id
        )
        # Safely close bot session
        await tg_service.close()
        
        return {
            "status": "success",
            "message": "Payment confirmed and subscription activated",
            "payment_id": payment.id,
            "subscription": {
                "id": subscription.id,
                "is_active": subscription.is_active,
                "end_date": subscription.end_date.isoformat()
            },
            "invite_link": invite_link
        }
    except ValueError as ve:
        await tg_service.close()
        raise HTTPException(status_code=400, detail=str(ve))
    except Exception as e:
        await tg_service.close()
        raise HTTPException(status_code=500, detail=f"Webhook processing error: {str(e)}")


@router.get("/subscriptions/me")
async def get_my_subscription(
    x_telegram_id: int = Header(..., alias="X-Telegram-Id"),
    db: AsyncSession = Depends(get_db)
):
    """Retrieve subscription status for current user based on header."""
    user_repo = UserRepository(db)
    sub_repo = SubscriptionRepository(db)
    
    user = await user_repo.get_by_telegram_id(x_telegram_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
        
    sub = await sub_repo.get_active_by_user(user.id)
    if not sub:
        return {
            "telegram_id": x_telegram_id,
            "has_active_subscription": False,
            "subscription": None
        }
        
    # Get package info
    result = await db.execute(select(Package).filter(Package.id == sub.package_id))
    package = result.scalars().first()
    
    return {
        "telegram_id": x_telegram_id,
        "has_active_subscription": sub.is_active,
        "subscription": {
            "start_date": sub.start_date.isoformat(),
            "end_date": sub.end_date.isoformat(),
            "package_name": package.name if package else "Custom",
            "is_active": sub.is_active
        }
    }
