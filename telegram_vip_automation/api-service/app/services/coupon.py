import logging
from datetime import datetime, timezone
from typing import Optional, Tuple
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy import func

from app.models.coupon import Coupon, CouponRedemption
from app.models.payment import Payment

logger = logging.getLogger(__name__)


class CouponService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def create_coupon(
        self,
        code: str,
        coupon_type: str,
        value: float,
        is_first_purchase_only: bool = False,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        max_usage: Optional[int] = None,
        per_user_limit: int = 1,
    ) -> Coupon:
        """Create a new discount or free trial coupon."""
        coupon = Coupon(
            code=code.strip().upper(),
            coupon_type=coupon_type,
            value=value,
            is_first_purchase_only=is_first_purchase_only,
            start_date=start_date,
            end_date=end_date,
            max_usage=max_usage,
            per_user_limit=per_user_limit,
            is_active=True,
        )
        self.db.add(coupon)
        await self.db.flush()
        return coupon

    async def get_by_code(self, code: str) -> Optional[Coupon]:
        """Fetch coupon by its code (case-insensitive)."""
        result = await self.db.execute(
            select(Coupon).filter(func.upper(Coupon.code) == code.strip().upper())
        )
        return result.scalars().first()

    async def validate_coupon(self, code: str, user_id: int) -> Tuple[bool, str, Optional[Coupon]]:
        """Validate coupon eligibility for a user.

        Returns (is_valid, error_message, coupon_object).
        """
        coupon = await self.get_by_code(code)
        if not coupon:
            return False, "Coupon code not found", None

        if not coupon.is_active:
            return False, "Coupon is inactive", None

        # Check expiration dates
        now = datetime.now(timezone.utc)
        if coupon.start_date and coupon.start_date > now:
            return False, "Coupon is not active yet", None
        if coupon.end_date and coupon.end_date < now:
            return False, "Coupon has expired", None

        # Check total usage limits
        if coupon.max_usage is not None and coupon.current_usage >= coupon.max_usage:
            return False, "Coupon usage limit reached", None

        # Check per-user usage limits
        redemptions_res = await self.db.execute(
            select(func.count(CouponRedemption.id)).filter(
                CouponRedemption.coupon_id == coupon.id,
                CouponRedemption.user_id == user_id,
            )
        )
        user_redemptions = redemptions_res.scalar() or 0
        if user_redemptions >= coupon.per_user_limit:
            return False, "You have reached the usage limit for this coupon", None

        # Check first purchase only restriction
        if coupon.is_first_purchase_only:
            payments_res = await self.db.execute(
                select(func.count(Payment.id)).filter(
                    Payment.user_id == user_id, Payment.status == "paid"
                )
            )
            successful_payments = payments_res.scalar() or 0
            if successful_payments > 0:
                return False, "This coupon is only valid for first purchases", None

        return True, "", coupon

    async def calculate_discounted_amount(self, coupon: Coupon, original_amount: float) -> float:
        """Calculate amount after applying coupon discount."""
        if coupon.coupon_type == "percentage":
            discount = original_amount * (coupon.value / 100.0)
            return max(0.0, original_amount - discount)
        elif coupon.coupon_type == "fixed":
            return max(0.0, original_amount - coupon.value)
        elif coupon.coupon_type == "free_trial":
            return 0.0
        return original_amount

    async def redeem_coupon(self, code: str, user_id: int, payment_id: Optional[int] = None) -> Optional[CouponRedemption]:
        """Redeem a coupon, incrementing usage counter."""
        is_valid, err, coupon = await self.validate_coupon(code, user_id)
        if not is_valid or not coupon:
            raise ValueError(f"Coupon redemption failed: {err}")

        # Record redemption
        redemption = CouponRedemption(
            coupon_id=coupon.id,
            user_id=user_id,
            payment_id=payment_id,
        )
        self.db.add(redemption)

        # Increment current usage counter
        coupon.current_usage += 1
        await self.db.flush()
        return redemption

    async def list_coupons(self) -> list[Coupon]:
        """Fetch list of all coupons."""
        result = await self.db.execute(select(Coupon))
        return list(result.scalars().all())

    async def get_coupon(self, coupon_id: int) -> Optional[Coupon]:
        """Fetch coupon by ID."""
        return await self.db.get(Coupon, coupon_id)

    async def update_coupon(
        self,
        coupon_id: int,
        code: Optional[str] = None,
        coupon_type: Optional[str] = None,
        value: Optional[float] = None,
        is_active: Optional[bool] = None,
        is_first_purchase_only: Optional[bool] = None,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        max_usage: Optional[int] = None,
        per_user_limit: Optional[int] = None,
    ) -> Optional[Coupon]:
        """Update properties of an existing coupon."""
        coupon = await self.db.get(Coupon, coupon_id)
        if not coupon:
            return None

        if code is not None:
            coupon.code = code.strip().upper()
        if coupon_type is not None:
            coupon.coupon_type = coupon_type
        if value is not None:
            coupon.value = value
        if is_active is not None:
            coupon.is_active = is_active
        if is_first_purchase_only is not None:
            coupon.is_first_purchase_only = is_first_purchase_only
        if start_date is not None:
            coupon.start_date = start_date
        if end_date is not None:
            coupon.end_date = end_date
        if max_usage is not None:
            coupon.max_usage = max_usage
        if per_user_limit is not None:
            coupon.per_user_limit = per_user_limit

        await self.db.flush()
        return coupon

    async def delete_coupon(self, coupon_id: int) -> bool:
        """Delete coupon by ID."""
        coupon = await self.db.get(Coupon, coupon_id)
        if not coupon:
            return False
        await self.db.delete(coupon)
        await self.db.flush()
        return True
