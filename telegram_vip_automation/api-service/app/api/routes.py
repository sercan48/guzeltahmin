import logging
from fastapi import APIRouter, Depends, HTTPException, Header, status, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from typing import List, Dict, Any, Optional

from app.db.session import get_db
from app.models.product import ProductPackage
from app.repositories.user import UserRepository
from app.repositories.subscription import SubscriptionRepository
from app.services.telegram import TelegramService
from app.services.payment import PaymentService
from app.core.config import settings
from app.core.security import verify_hmac_signature

from shared.dtos import (
    PackageResponse,
    PaymentCreate,
    PaymentResponse,
    WebhookPayload,
    SubscriptionStatusResponse,
    SubscriptionInfo,
    EventCreate
)

logger = logging.getLogger(__name__)
router = APIRouter()


async def verify_internal_token(x_internal_token: str = Header(..., alias="X-Internal-Token")):
    """Legacy token auth kept for external webhook endpoints."""
    if x_internal_token != settings.INTERNAL_API_TOKEN:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing internal token"
        )


# ── Public endpoints (no auth) ────────────────────────────────────────────

@router.get("/health")
async def health_check(db: AsyncSession = Depends(get_db)):
    """Health check endpoint checking DB connectivity."""
    try:
        await db.execute(select(1))
        return {"status": "healthy", "database": "connected"}
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Database connection failed: {str(e)}"
        )


@router.get("/packages", response_model=List[PackageResponse])
async def get_packages(db: AsyncSession = Depends(get_db)):
    """Fetch all active packages available for membership."""
    result = await db.execute(select(ProductPackage).filter(ProductPackage.is_active == True))
    packages = result.scalars().all()
    return packages


# ── HMAC-authenticated internal endpoints ──────────────────────────────────

@router.post("/payments/mock", response_model=PaymentResponse)
async def create_payment(
    payload: PaymentCreate,
    db: AsyncSession = Depends(get_db),
    _=Depends(verify_hmac_signature)
):
    """Internal endpoint: Create a pending mock payment record (HMAC auth)."""
    tg_service = TelegramService()
    pay_service = PaymentService(db, tg_service)
    try:
        payment = await pay_service.create_mock_payment(
            telegram_id=payload.telegram_id,
            package_id=payload.package_id,
            idempotency_key=payload.idempotency_key,
            username=payload.username or "",
            first_name=payload.first_name or "",
            last_name=payload.last_name or ""
        )
        await tg_service.close()
        return payment
    except ValueError as ve:
        await tg_service.close()
        raise HTTPException(status_code=400, detail=str(ve))
    except Exception as e:
        await tg_service.close()
        raise HTTPException(status_code=500, detail=f"Failed to create payment: {str(e)}")


# ── Webhook endpoint (legacy token auth for external providers) ────────────

@router.post("/payments/webhook/mock")
async def mock_payment_webhook(
    payload: WebhookPayload,
    db: AsyncSession = Depends(get_db),
    _=Depends(verify_internal_token)
):
    """Mock webhook to simulate payment confirmation from gateways (token auth)."""
    tg_service = TelegramService()
    pay_service = PaymentService(db, tg_service)

    try:
        payment, subscription, invite_link = await pay_service.confirm_payment(
            provider_tx_id=payload.provider_tx_id
        )
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


# ── HMAC-authenticated subscription endpoints ─────────────────────────────

@router.get("/subscriptions/status", response_model=SubscriptionStatusResponse)
async def get_subscription_status(
    x_telegram_id: int = Header(..., alias="X-Telegram-Id"),
    db: AsyncSession = Depends(get_db),
    _=Depends(verify_hmac_signature)
):
    """Retrieve subscription status for current user (HMAC auth)."""
    user_repo = UserRepository(db)
    sub_repo = SubscriptionRepository(db)

    user = await user_repo.get_by_telegram_id(x_telegram_id)
    if not user:
        return SubscriptionStatusResponse(
            telegram_id=x_telegram_id,
            has_active_subscription=False,
            subscription=None
        )

    sub = await sub_repo.get_active_by_user(user.id)
    if not sub:
        return SubscriptionStatusResponse(
            telegram_id=x_telegram_id,
            has_active_subscription=False,
            subscription=None
        )

    result = await db.execute(select(ProductPackage).filter(ProductPackage.id == sub.package_id))
    package = result.scalars().first()

    sub_info = SubscriptionInfo(
        id=sub.id,
        start_date=sub.start_date,
        end_date=sub.end_date,
        package_name=package.name if package else "Custom",
        is_active=sub.is_active
    )

    return SubscriptionStatusResponse(
        telegram_id=x_telegram_id,
        has_active_subscription=sub.is_active,
        subscription=sub_info
    )


@router.get("/subscriptions/expired")
async def get_expired_subscriptions(
    db: AsyncSession = Depends(get_db),
    _=Depends(verify_hmac_signature)
):
    """List all active subscriptions whose end date has passed (HMAC auth)."""
    sub_repo = SubscriptionRepository(db)
    expired_subs = await sub_repo.list_expired_active_subscriptions()

    response_data = []
    user_repo = UserRepository(db)
    for sub in expired_subs:
        user = await user_repo.get(sub.user_id)
        if user:
            # Fetch all channel telegram IDs linked to this subscription's product
            from app.models.product import ProductChannel
            from app.models.channel import TelegramChannel
            chan_res = await db.execute(
                select(TelegramChannel.telegram_id)
                .join(ProductChannel, ProductChannel.channel_id == TelegramChannel.id)
                .filter(ProductChannel.product_id == sub.product_id)
            )
            channel_ids = [str(tid) for tid in chan_res.scalars().all()]
            if not channel_ids:
                channel_ids = [str(settings.VIP_CHANNEL_ID)]
                
            response_data.append({
                "subscription_id": sub.id,
                "user_id": sub.user_id,
                "telegram_id": user.telegram_id,
                "end_date": sub.end_date.isoformat(),
                "channels": channel_ids
            })
    return response_data


@router.post("/subscriptions/deactivate/{subscription_id}")
async def deactivate_subscription(
    subscription_id: int,
    db: AsyncSession = Depends(get_db),
    _=Depends(verify_hmac_signature)
):
    """Deactivate a specific subscription (HMAC auth)."""
    sub_repo = SubscriptionRepository(db)
    sub = await sub_repo.get(subscription_id)
    if not sub:
        raise HTTPException(status_code=404, detail="Subscription not found")

    sub.is_active = False
    await sub_repo.update(sub)
    return {"status": "success", "message": f"Subscription {subscription_id} marked inactive."}


# ── SaaS GROWTH & ADMIN API SCHEMAS ────────────────────────────────────────

from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime

class AdminLoginRequest(BaseModel):
    username: str
    password: str

class AdminTokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"

class AdminRefreshRequest(BaseModel):
    refresh_token: str

class CouponCreateRequest(BaseModel):
    code: str
    coupon_type: str
    value: float
    is_first_purchase_only: bool = False
    start_date: Optional[datetime] = None
    end_date: Optional[datetime] = None
    max_usage: Optional[int] = None
    per_user_limit: int = 1

class ValidateCouponRequest(BaseModel):
    code: str
    telegram_id: int
    payment_id: Optional[int] = None

class ClaimTrialRequest(BaseModel):
    telegram_id: int

class ReferralClickRequest(BaseModel):
    telegram_id: int
    code: str

class OnboardAffiliateRequest(BaseModel):
    telegram_id: int
    code: str
    commission_type: str = "percentage"
    commission_value: float = 20.0

class PayoutRequest(BaseModel):
    affiliate_id: int
    amount: float


class GrantDaysRequest(BaseModel):
    days: int
    product_id: int


class ExtendSubscriptionRequest(BaseModel):
    subscription_id: int
    days: int


class CancelSubscriptionRequest(BaseModel):
    subscription_id: int


class CouponUpdateRequest(BaseModel):
    code: Optional[str] = None
    coupon_type: Optional[str] = None
    value: Optional[float] = None
    is_active: Optional[bool] = None
    is_first_purchase_only: Optional[bool] = None
    start_date: Optional[datetime] = None
    end_date: Optional[datetime] = None
    max_usage: Optional[int] = None
    per_user_limit: Optional[int] = None


class AffiliatePayoutRequest(BaseModel):
    amount: float


# ── ADMIN PANEL AUTHENTICATION & RBAC ──────────────────────────────────────

from app.core.admin_auth import (
    hash_password,
    verify_password,
    create_access_token,
    create_refresh_token,
    decode_token,
    blacklist_token,
    is_token_blacklisted,
    require_admin,
    require_support,
    require_finance,
    require_any_role,
)
from app.models.admin import Admin
from app.services.coupon import CouponService
from app.services.referral import ReferralService
from app.services.trial import TrialService
from app.services.affiliate import AffiliateService
from app.services.analytics import AnalyticsService

@router.post("/admin/auth/login", response_model=AdminTokenResponse)
async def admin_login(payload: AdminLoginRequest, db: AsyncSession = Depends(get_db)):
    """Authenticate admin and return access/refresh tokens."""
    result = await db.execute(select(Admin).filter(Admin.username == payload.username, Admin.is_active == True))
    admin = result.scalars().first()
    if not admin or not verify_password(payload.password, admin.password_hash):
        raise HTTPException(status_code=401, detail="Invalid username or password")
    
    access = create_access_token(admin.id, admin.role)
    refresh = create_refresh_token(admin.id, admin.role)
    return AdminTokenResponse(access_token=access, refresh_token=refresh)


@router.post("/admin/auth/refresh", response_model=AdminTokenResponse)
async def admin_refresh(payload: AdminRefreshRequest, db: AsyncSession = Depends(get_db)):
    """Validate refresh token and issue new access/refresh tokens."""
    if await is_token_blacklisted(payload.refresh_token):
        raise HTTPException(status_code=401, detail="Refresh token revoked")
        
    try:
        decoded = decode_token(payload.refresh_token)
        if decoded.get("type") != "refresh":
            raise HTTPException(status_code=401, detail="Invalid token type")
        admin_id = int(decoded["sub"])
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid refresh token")
        
    result = await db.execute(select(Admin).filter(Admin.id == admin_id, Admin.is_active == True))
    admin = result.scalars().first()
    if not admin:
        raise HTTPException(status_code=401, detail="Admin inactive or not found")
        
    # Blacklist old refresh token
    import jwt
    try:
        # Get remaining time until exp
        exp_ts = decoded.get("exp", 0)
        remaining = int(exp_ts - datetime.now(timezone.utc).timestamp()) if exp_ts else 3600
        await blacklist_token(payload.refresh_token, remaining)
    except Exception:
        pass
        
    access = create_access_token(admin.id, admin.role)
    refresh = create_refresh_token(admin.id, admin.role)
    return AdminTokenResponse(access_token=access, refresh_token=refresh)


@router.post("/admin/auth/logout")
async def admin_logout(
    token_str: str = Header(..., alias="Authorization"),
    db: AsyncSession = Depends(get_db),
    admin: Admin = Depends(require_any_role)
):
    """Revoke/blacklist the admin access token."""
    clean_token = token_str.replace("Bearer ", "").strip()
    try:
        decoded = decode_token(clean_token)
        exp_ts = decoded.get("exp", 0)
        from datetime import timezone
        remaining = int(exp_ts - datetime.now(timezone.utc).timestamp()) if exp_ts else 3600
        await blacklist_token(clean_token, remaining)
    except Exception:
        pass
    return {"status": "success", "message": "Logged out successfully"}


# ── ADMIN PANEL CRUD ENDPOINTS (RBAC) ──────────────────────────────────────

@router.get("/admin/users")
async def admin_get_users(
    db: AsyncSession = Depends(get_db),
    _=Depends(require_support)
):
    """Retrieve users list for admin dashboard."""
    from app.models.user import User
    result = await db.execute(select(User))
    users = result.scalars().all()
    return [{"id": u.id, "telegram_id": u.telegram_id, "username": u.username, "first_name": u.first_name, "last_name": u.last_name, "trial_used": u.trial_used, "referred_by_id": u.referred_by_id} for u in users]


@router.get("/admin/subscriptions")
async def admin_get_subscriptions(
    db: AsyncSession = Depends(get_db),
    _=Depends(require_support)
):
    """Retrieve all subscriptions list for admin dashboard."""
    from app.models.subscription import Subscription
    result = await db.execute(select(Subscription))
    subs = result.scalars().all()
    return [{"id": s.id, "user_id": s.user_id, "package_id": s.package_id, "channel_id": s.channel_id, "start_date": s.start_date.isoformat(), "end_date": s.end_date.isoformat(), "is_active": s.is_active} for s in subs]


@router.post("/admin/coupons")
async def admin_create_coupon(
    payload: CouponCreateRequest,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_finance)
):
    """Create a new coupon (percentage/fixed/trial)."""
    service = CouponService(db)
    coupon = await service.create_coupon(
        code=payload.code,
        coupon_type=payload.coupon_type,
        value=payload.value,
        is_first_purchase_only=payload.is_first_purchase_only,
        start_date=payload.start_date,
        end_date=payload.end_date,
        max_usage=payload.max_usage,
        per_user_limit=payload.per_user_limit,
    )
    return {"status": "success", "coupon_id": coupon.id, "code": coupon.code}


@router.get("/admin/affiliates")
async def admin_get_affiliates(
    db: AsyncSession = Depends(get_db),
    _=Depends(require_finance)
):
    """Retrieve affiliate programs list and earnings."""
    from app.models.affiliate import Affiliate, AffiliateCommission, AffiliatePayout
    result_aff = await db.execute(select(Affiliate))
    affs = result_aff.scalars().all()
    
    result_payouts = await db.execute(select(AffiliatePayout))
    payouts = result_payouts.scalars().all()
    
    return {
        "affiliates": [{"id": a.id, "user_id": a.user_id, "code": a.code, "commission_type": a.commission_type, "commission_value": a.commission_value, "is_active": a.is_active} for a in affs],
        "payout_requests": [{"id": p.id, "affiliate_id": p.affiliate_id, "amount": p.amount, "status": p.status, "paid_at": p.paid_at.isoformat() if p.paid_at else None} for p in payouts]
    }


@router.post("/admin/affiliates/payouts/review/{payout_id}")
async def admin_review_payout(
    payout_id: int,
    approve: bool,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_finance)
):
    """Approve or reject a pending affiliate payout."""
    service = AffiliateService(db)
    success = await service.process_payout_status(payout_id, approve)
    if not success:
        raise HTTPException(status_code=400, detail="Invalid payout or payout already processed")
    return {"status": "success", "message": f"Payout request {payout_id} {'approved' if approve else 'rejected'}."}


# ── NEW ADMIN PLATFORM ROUTES ──────────────────────────────────────────────

from app.services.admin import AdminService

@router.get("/admin/users/{id}")
async def admin_get_user_detail(
    id: int,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_support)
):
    from app.models.user import User
    result = await db.execute(select(User).filter(User.id == id))
    user = result.scalars().first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return {
        "id": user.id,
        "telegram_id": user.telegram_id,
        "username": user.username,
        "first_name": user.first_name,
        "last_name": user.last_name,
        "trial_used": user.trial_used,
        "is_suspended": user.is_suspended,
        "referred_by_id": user.referred_by_id
    }


@router.post("/admin/users/{id}/suspend")
async def admin_suspend_user(
    id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin: Admin = Depends(require_admin)
):
    service = AdminService(db)
    success = await service.suspend_user(
        user_id=id,
        admin_id=admin.id,
        ip_address=request.client.host if request.client else None
    )
    if not success:
        raise HTTPException(status_code=404, detail="User not found")
    return {"status": "success", "message": f"User {id} suspended successfully."}


@router.post("/admin/users/{id}/unsuspend")
async def admin_unsuspend_user(
    id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin: Admin = Depends(require_admin)
):
    service = AdminService(db)
    success = await service.unsuspend_user(
        user_id=id,
        admin_id=admin.id,
        ip_address=request.client.host if request.client else None
    )
    if not success:
        raise HTTPException(status_code=404, detail="User not found")
    return {"status": "success", "message": f"User {id} unsuspended successfully."}


@router.post("/admin/users/{id}/grant-days")
async def admin_grant_days(
    id: int,
    payload: GrantDaysRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin: Admin = Depends(require_admin)
):
    service = AdminService(db)
    success = await service.grant_days(
        user_id=id,
        days=payload.days,
        product_id=payload.product_id,
        admin_id=admin.id,
        ip_address=request.client.host if request.client else None
    )
    if not success:
        raise HTTPException(status_code=404, detail="User or Product not found")
    return {"status": "success", "message": f"Granted {payload.days} days to user {id}."}


@router.post("/admin/subscriptions/extend")
async def admin_extend_subscription(
    payload: ExtendSubscriptionRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin: Admin = Depends(require_admin)
):
    service = AdminService(db)
    success = await service.extend_subscription(
        subscription_id=payload.subscription_id,
        days=payload.days,
        admin_id=admin.id,
        ip_address=request.client.host if request.client else None
    )
    if not success:
        raise HTTPException(status_code=404, detail="Subscription not found")
    return {"status": "success", "message": f"Subscription {payload.subscription_id} extended by {payload.days} days."}


@router.post("/admin/subscriptions/cancel")
async def admin_cancel_subscription(
    payload: CancelSubscriptionRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin: Admin = Depends(require_admin)
):
    service = AdminService(db)
    success = await service.cancel_subscription(
        subscription_id=payload.subscription_id,
        admin_id=admin.id,
        ip_address=request.client.host if request.client else None
    )
    if not success:
        raise HTTPException(status_code=404, detail="Subscription not found")
    return {"status": "success", "message": f"Subscription {payload.subscription_id} cancelled successfully."}


@router.get("/admin/payments")
async def admin_get_payments(
    db: AsyncSession = Depends(get_db),
    _=Depends(require_finance)
):
    from app.models.payment import Payment
    result = await db.execute(select(Payment))
    payments = result.scalars().all()
    return [{
        "id": p.id,
        "user_id": p.user_id,
        "package_id": p.package_id,
        "amount": p.amount,
        "currency": p.currency,
        "status": p.status,
        "provider_tx_id": p.provider_tx_id,
        "idempotency_key": p.idempotency_key,
        "coupon_id": p.coupon_id
    } for p in payments]


@router.get("/admin/payments/{id}")
async def admin_get_payment_detail(
    id: int,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_finance)
):
    from app.models.payment import Payment
    result = await db.execute(select(Payment).filter(Payment.id == id))
    p = result.scalars().first()
    if not p:
        raise HTTPException(status_code=404, detail="Payment not found")
    return {
        "id": p.id,
        "user_id": p.user_id,
        "package_id": p.package_id,
        "amount": p.amount,
        "currency": p.currency,
        "status": p.status,
        "provider_tx_id": p.provider_tx_id,
        "idempotency_key": p.idempotency_key,
        "coupon_id": p.coupon_id
    }


@router.get("/admin/coupons")
async def admin_list_coupons(
    db: AsyncSession = Depends(get_db),
    _=Depends(require_finance)
):
    service = CouponService(db)
    coupons = await service.list_coupons()
    return [{
        "id": c.id,
        "code": c.code,
        "coupon_type": c.coupon_type,
        "value": c.value,
        "is_first_purchase_only": c.is_first_purchase_only,
        "start_date": c.start_date.isoformat() if c.start_date else None,
        "end_date": c.end_date.isoformat() if c.end_date else None,
        "max_usage": c.max_usage,
        "current_usage": c.current_usage,
        "per_user_limit": c.per_user_limit,
        "is_active": c.is_active
    } for c in coupons]


@router.get("/admin/coupons/{id}")
async def admin_get_coupon_detail(
    id: int,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_finance)
):
    service = CouponService(db)
    c = await service.get_coupon(id)
    if not c:
        raise HTTPException(status_code=404, detail="Coupon not found")
    return {
        "id": c.id,
        "code": c.code,
        "coupon_type": c.coupon_type,
        "value": c.value,
        "is_first_purchase_only": c.is_first_purchase_only,
        "start_date": c.start_date.isoformat() if c.start_date else None,
        "end_date": c.end_date.isoformat() if c.end_date else None,
        "max_usage": c.max_usage,
        "current_usage": c.current_usage,
        "per_user_limit": c.per_user_limit,
        "is_active": c.is_active
    }


@router.put("/admin/coupons/{id}")
async def admin_update_coupon(
    id: int,
    payload: CouponUpdateRequest,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_finance)
):
    service = CouponService(db)
    coupon = await service.update_coupon(
        coupon_id=id,
        code=payload.code,
        coupon_type=payload.coupon_type,
        value=payload.value,
        is_active=payload.is_active,
        is_first_purchase_only=payload.is_first_purchase_only,
        start_date=payload.start_date,
        end_date=payload.end_date,
        max_usage=payload.max_usage,
        per_user_limit=payload.per_user_limit
    )
    if not coupon:
        raise HTTPException(status_code=404, detail="Coupon not found")
    return {"status": "success", "message": f"Coupon {id} updated successfully."}


@router.delete("/admin/coupons/{id}")
async def admin_delete_coupon(
    id: int,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_finance)
):
    service = CouponService(db)
    success = await service.delete_coupon(id)
    if not success:
        raise HTTPException(status_code=404, detail="Coupon not found")
    return {"status": "success", "message": f"Coupon {id} deleted successfully."}


@router.get("/admin/affiliates/{id}")
async def admin_get_affiliate_detail(
    id: int,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_finance)
):
    from app.models.affiliate import Affiliate, AffiliateCommission, AffiliatePayout
    
    aff_res = await db.execute(select(Affiliate).filter(Affiliate.id == id))
    affiliate = aff_res.scalars().first()
    if not affiliate:
        raise HTTPException(status_code=404, detail="Affiliate not found")
        
    comm_res = await db.execute(select(AffiliateCommission).filter(AffiliateCommission.affiliate_id == id))
    commissions = comm_res.scalars().all()
    
    payout_res = await db.execute(select(AffiliatePayout).filter(AffiliatePayout.affiliate_id == id))
    payouts = payout_res.scalars().all()
    
    return {
        "id": affiliate.id,
        "user_id": affiliate.user_id,
        "code": affiliate.code,
        "commission_type": affiliate.commission_type,
        "commission_value": affiliate.commission_value,
        "is_active": affiliate.is_active,
        "commissions": [{"id": c.id, "payment_id": c.payment_id, "amount": c.amount, "status": c.status} for c in commissions],
        "payouts": [{"id": p.id, "amount": p.amount, "status": p.status, "paid_at": p.paid_at.isoformat() if p.paid_at else None} for p in payouts]
    }


@router.post("/admin/affiliates/{id}/payout")
async def admin_request_affiliate_payout(
    id: int,
    payload: AffiliatePayoutRequest,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_finance)
):
    service = AffiliateService(db)
    try:
        payout = await service.request_payout(affiliate_id=id, amount=payload.amount)
        return {"status": "success", "payout_id": payout.id, "amount": payout.amount, "payout_status": payout.status}
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))


@router.get("/admin/audit-logs")
async def admin_get_audit_logs(
    db: AsyncSession = Depends(get_db),
    _=Depends(require_admin)
):
    from app.models.audit_log import AdminAuditLog
    result = await db.execute(select(AdminAuditLog).order_by(AdminAuditLog.created_at.desc()))
    logs = result.scalars().all()
    return [{
        "id": log.id,
        "admin_id": log.admin_id,
        "action": log.action,
        "target_type": log.target_type,
        "target_id": log.target_id,
        "details": log.details,
        "ip_address": log.ip_address,
        "created_at": log.created_at.isoformat()
    } for log in logs]


@router.get("/admin/referrals")
async def admin_get_referrals(
    db: AsyncSession = Depends(get_db),
    _=Depends(require_support)
):
    """List referral events, showing flagged cases for review."""
    from app.models.referral import ReferralEvent
    result = await db.execute(select(ReferralEvent))
    events = result.scalars().all()
    return [{"id": e.id, "referrer_id": e.referrer_id, "referred_id": e.referred_id, "status": e.status, "risk_score": e.risk_score, "fraud_details": e.fraud_details} for e in events]


@router.post("/admin/referrals/review/{event_id}")
async def admin_review_referral(
    event_id: int,
    approve: bool,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_admin)
):
    """Approve or reject a fraud_flagged referral reward."""
    service = ReferralService(db)
    success = await service.review_referral_event(event_id, approve)
    if not success:
        raise HTTPException(status_code=400, detail="Invalid event or not in fraud_flagged status")
    return {"status": "success", "message": f"Referral event {event_id} {'approved' if approve else 'rejected'}."}


# ── SAAS METRICS & ANALYTICS ENDPOINTS ─────────────────────────────────────

@router.get("/analytics/overview")
async def get_analytics_overview(
    db: AsyncSession = Depends(get_db),
    _=Depends(require_finance)
):
    """Get high-level SaaS growth metrics (MRR, ARR, Conversion, Churn)."""
    service = AnalyticsService(db)
    return await service.get_overview_metrics()


@router.get("/analytics/revenue")
async def get_analytics_revenue(
    db: AsyncSession = Depends(get_db),
    _=Depends(require_finance)
):
    """Get ciro breakdowns (organic vs affiliate vs referral)."""
    service = AnalyticsService(db)
    return await service.get_revenue_metrics()


@router.get("/analytics/referrals")
async def get_analytics_referrals(
    db: AsyncSession = Depends(get_db),
    _=Depends(require_finance)
):
    """Get general stats about referrals performance."""
    service = AnalyticsService(db)
    return await service.get_referral_metrics()


@router.get("/analytics/churn")
async def get_analytics_churn(
    db: AsyncSession = Depends(get_db),
    _=Depends(require_finance)
):
    """Get overall subscription renewal and churn metrics."""
    service = AnalyticsService(db)
    return await service.get_churn_metrics()


@router.get("/analytics/affiliates")
async def get_analytics_affiliates(
    db: AsyncSession = Depends(get_db),
    _=Depends(require_finance)
):
    """Get general affiliate statistics."""
    service = AnalyticsService(db)
    return await service.get_affiliate_analytics()


@router.get("/analytics/trials")
async def get_analytics_trials(
    db: AsyncSession = Depends(get_db),
    _=Depends(require_finance)
):
    """Get trial conversions and trial activity statistics."""
    service = AnalyticsService(db)
    return await service.get_trial_analytics()


@router.get("/analytics/cohorts")
async def get_analytics_cohorts(
    db: AsyncSession = Depends(get_db),
    _=Depends(require_finance)
):
    """Get monthly cohort retention analytics."""
    service = AnalyticsService(db)
    return await service.get_cohort_retention()


@router.get("/analytics/churn-risk")
async def get_analytics_churn_risk(
    db: AsyncSession = Depends(get_db),
    _=Depends(require_finance)
):
    """Get computed user churn risk scores."""
    service = AnalyticsService(db)
    # First, run calculation to ensure we have fresh data
    await service.calculate_and_save_all_risk_scores()
    return await service.get_churn_risk_scores()


@router.post("/analytics/calculate-risk")
async def trigger_calculate_risk(
    db: AsyncSession = Depends(get_db),
    _=Depends(verify_hmac_signature)
):
    """Recalculate and persist churn risk scores for all users."""
    service = AnalyticsService(db)
    await service.calculate_and_save_all_risk_scores()
    return {"status": "success", "message": "Churn risk calculation completed."}


@router.post("/analytics/refresh-views")
async def trigger_refresh_views(
    db: AsyncSession = Depends(get_db),
    _=Depends(verify_hmac_signature)
):
    """Refresh all SaaS materialized views concurrently (HMAC auth)."""
    service = AnalyticsService(db)
    await service.refresh_materialized_views()
    return {"status": "success", "message": "Materialized views refreshed."}


@router.post("/reconciliation/run")
async def trigger_reconciliation(
    db: AsyncSession = Depends(get_db),
    _=Depends(verify_hmac_signature)
):
    """Run daily data consistency audit and auto-repair (HMAC auth)."""
    from app.services.reconciliation import ReconciliationService
    service = ReconciliationService(db)
    stats = await service.run_reconciliation()
    return {"status": "success", "message": "Reconciliation completed.", "stats": stats}




# ── BOT-SERVICE INTERACTION ENDPOINTS (HMAC AUTH) ──────────────────────────

@router.post("/bot/trial/claim")
async def bot_claim_trial(
    payload: ClaimTrialRequest,
    db: AsyncSession = Depends(get_db),
    _=Depends(verify_hmac_signature)
):
    """Claim a 3-day free trial subscription (HMAC auth)."""
    service = TrialService(db)
    success, message, sub, invite_link = await service.claim_free_trial(payload.telegram_id)
    if not success:
        raise HTTPException(status_code=400, detail=message)
    return {
        "status": "success",
        "message": message,
        "subscription": {
            "id": sub.id if sub else None,
            "end_date": sub.end_date.isoformat() if sub else None
        },
        "invite_link": invite_link
    }


@router.post("/bot/coupon/validate")
async def bot_validate_coupon(
    payload: ValidateCouponRequest,
    db: AsyncSession = Depends(get_db),
    _=Depends(verify_hmac_signature)
):
    """Validate coupon eligibility and return discounted pricing details (HMAC auth)."""
    from app.models.user import User
    from app.models.payment import Payment

    # Resolve database user.id from telegram_id
    result_user = await db.execute(select(User).filter(User.telegram_id == payload.telegram_id))
    user = result_user.scalars().first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    service = CouponService(db)
    success, message, coupon = await service.validate_coupon(payload.code, user.id)
    if not success or not coupon:
        raise HTTPException(status_code=400, detail=message)

    discounted_amount = None
    if payload.payment_id is not None:
        payment_res = await db.execute(select(Payment).filter(Payment.id == payload.payment_id))
        payment = payment_res.scalars().first()
        if not payment:
            raise HTTPException(status_code=404, detail="Payment not found")
        if payment.user_id != user.id:
            raise HTTPException(status_code=400, detail="Payment user mismatch")
        if payment.status != "pending":
            raise HTTPException(status_code=400, detail="Payment is not pending")

        # Calculate discount
        discounted_amount = await service.calculate_discounted_amount(coupon, payment.amount)
        payment.amount = discounted_amount
        payment.coupon_id = coupon.id
        await db.flush()

    return {
        "status": "success",
        "coupon": {
            "code": coupon.code,
            "coupon_type": coupon.coupon_type,
            "value": coupon.value
        },
        "discounted_amount": discounted_amount
    }


@router.get("/bot/referral/code")
async def bot_get_referral_code(
    x_telegram_id: int = Header(..., alias="X-Telegram-Id"),
    db: AsyncSession = Depends(get_db),
    _=Depends(verify_hmac_signature)
):
    """Retrieve or generate referral code for current bot user (HMAC auth)."""
    # Find user record first
    result = await db.execute(select(User).filter(User.telegram_id == x_telegram_id))
    user = result.scalars().first()
    if not user:
        raise HTTPException(status_code=404, detail="User not registered in the system")
        
    service = ReferralService(db)
    ref_code = await service.get_or_create_code(user.id)
    return {"status": "success", "code": ref_code.code}


@router.post("/bot/referral/click")
async def bot_log_referral_click(
    payload: ReferralClickRequest,
    db: AsyncSession = Depends(get_db),
    _=Depends(verify_hmac_signature)
):
    """Log starting bot with a referral code (HMAC auth)."""
    service = ReferralService(db)
    success, message = await service.log_referral_click(payload.telegram_id, payload.code)
    if not success:
        raise HTTPException(status_code=400, detail=message)
    return {"status": "success", "message": message}


# ── BOT-SERVICE CAMPAIGN ENDPOINTS (HMAC AUTH) ─────────────────────────────

class LogNotificationRequest(BaseModel):
    subscription_id: int
    user_id: int
    notification_type: str

from app.services.campaign import CampaignService

@router.get("/bot/campaigns/pending")
async def bot_get_pending_campaigns(
    db: AsyncSession = Depends(get_db),
    _=Depends(verify_hmac_signature)
):
    """Fetch all pending renewal notifications (HMAC auth)."""
    service = CampaignService(db)
    return await service.get_pending_notifications()


@router.post("/bot/campaigns/log")
async def bot_log_campaign(
    payload: LogNotificationRequest,
    db: AsyncSession = Depends(get_db),
    _=Depends(verify_hmac_signature)
):
    """Log a sent renewal notification to prevent duplicates (HMAC auth)."""
    service = CampaignService(db)
    log = await service.log_sent_notification(
        subscription_id=payload.subscription_id,
        user_id=payload.user_id,
        notification_type=payload.notification_type
    )
    return {"status": "success", "log_id": log.id}


class CampaignExecutionResult(BaseModel):
    status: str  # sent or failed


@router.get("/bot/campaign-executions/pending")
async def bot_get_pending_campaign_executions(
    db: AsyncSession = Depends(get_db),
    _=Depends(verify_hmac_signature)
):
    """Fetch pending campaign executions that are due for delivery."""
    from app.models.campaign import CampaignExecution, CampaignRule, Campaign
    from app.models.user import User
    
    now = datetime.now(timezone.utc)
    # Join to get user telegram_id and rule template details
    result = await db.execute(
        select(CampaignExecution, User, CampaignRule)
        .join(User, CampaignExecution.user_id == User.id)
        .join(Campaign, CampaignExecution.campaign_id == Campaign.id)
        .join(CampaignRule, CampaignRule.campaign_id == Campaign.id)
        .filter(CampaignExecution.status == "pending", CampaignExecution.executed_at <= now, Campaign.is_active == True)
    )
    
    pending = []
    for exec_rec, user, rule in result.all():
        message = rule.message_template
        if rule.coupon_code:
            message = message.replace("{coupon_code}", rule.coupon_code).replace("{coupon}", rule.coupon_code)
            
        pending.append({
            "execution_id": exec_rec.id,
            "user_id": user.id,
            "telegram_id": user.telegram_id,
            "message": message
        })
    return pending


@router.post("/bot/campaign-executions/{id}/complete")
async def bot_complete_campaign_execution(
    id: int,
    payload: CampaignExecutionResult,
    db: AsyncSession = Depends(get_db),
    _=Depends(verify_hmac_signature)
):
    """Mark a campaign execution as sent or failed."""
    from app.models.campaign import CampaignExecution
    exec_rec = await db.get(CampaignExecution, id)
    if not exec_rec:
        raise HTTPException(status_code=404, detail="Campaign execution record not found")
        
    exec_rec.status = payload.status
    if payload.status == "sent":
        exec_rec.executed_at = datetime.now(timezone.utc)
    await db.flush()
    return {"status": "success"}


@router.post("/events")
async def publish_event(
    payload: EventCreate,
    db: AsyncSession = Depends(get_db),
    _=Depends(verify_hmac_signature)
):
    """Publish a domain event from Bot or Worker service (HMAC auth)."""
    from shared.dtos import EventCreate
    from app.services.event import EventService

    service = EventService(db)
    event = await service.log_event(
        event_type=payload.event_type,
        user_id=payload.user_id,
        payload_json=payload.payload_json,
        correlation_id=payload.correlation_id,
        source_service=payload.source_service
    )
    return {"status": "success", "event_id": event.id}


