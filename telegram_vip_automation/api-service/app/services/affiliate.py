import logging
from datetime import datetime, timezone
from typing import Optional, List
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy import func

from app.models.user import User
from app.models.affiliate import Affiliate, AffiliateCommission, AffiliatePayout
from app.models.payment import Payment

logger = logging.getLogger(__name__)


class AffiliateService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def onboard_affiliate(
        self,
        user_id: int,
        code: str,
        commission_type: str = "percentage",
        commission_value: float = 20.0,
    ) -> Affiliate:
        """Register a user as an affiliate."""
        existing = await self.db.execute(
            select(Affiliate).filter(Affiliate.user_id == user_id)
        )
        aff = existing.scalars().first()
        if aff:
            return aff

        # Check code uniqueness
        code_check = await self.db.execute(
            select(Affiliate).filter(Affiliate.code == code.strip().upper())
        )
        if code_check.scalars().first():
            raise ValueError("Affiliate code already taken")

        aff = Affiliate(
            user_id=user_id,
            code=code.strip().upper(),
            commission_type=commission_type,
            commission_value=commission_value,
            is_active=True,
        )
        self.db.add(aff)
        await self.db.flush()
        return aff

    async def get_by_code(self, code: str) -> Optional[Affiliate]:
        """Fetch affiliate by code."""
        result = await self.db.execute(
            select(Affiliate).filter(Affiliate.code == code.strip().upper())
        )
        return result.scalars().first()

    async def process_commission_on_payment(self, payment_id: int) -> Optional[AffiliateCommission]:
        """Create affiliate commission if the user was referred by an affiliate."""
        payment = await self.db.get(Payment, payment_id)
        if not payment or payment.status != "paid":
            return None

        # Fetch paying user
        user = await self.db.get(User, payment.user_id)
        if not user or not user.referred_by_id:
            return None

        # Check if the referrer is an active affiliate
        aff_res = await self.db.execute(
            select(Affiliate).filter(
                Affiliate.user_id == user.referred_by_id, Affiliate.is_active == True
            )
        )
        affiliate = aff_res.scalars().first()
        if not affiliate:
            return None

        # Check if commission was already created for this payment
        comm_check = await self.db.execute(
            select(AffiliateCommission).filter(AffiliateCommission.payment_id == payment.id)
        )
        if comm_check.scalars().first():
            return None

        # Calculate commission amount
        if affiliate.commission_type == "percentage":
            commission_amount = payment.amount * (affiliate.commission_value / 100.0)
        elif affiliate.commission_type == "fixed":
            commission_amount = min(payment.amount, affiliate.commission_value)
        else:
            commission_amount = 0.0

        commission = AffiliateCommission(
            affiliate_id=affiliate.id,
            payment_id=payment.id,
            amount=commission_amount,
            status="pending",
        )
        self.db.add(commission)
        await self.db.flush()

        logger.info(
            "affiliate_commission_created",
            extra={
                "affiliate_id": affiliate.id,
                "payment_id": payment.id,
                "amount": commission_amount,
            },
        )
        return commission

    async def request_payout(self, affiliate_id: int, amount: float) -> AffiliatePayout:
        """Create a payout request for an affiliate."""
        affiliate = await self.db.get(Affiliate, affiliate_id)
        if not affiliate:
            raise ValueError("Affiliate not found")

        # Verify earnings balance vs payout total
        earnings_res = await self.db.execute(
            select(func.sum(AffiliateCommission.amount)).filter(
                AffiliateCommission.affiliate_id == affiliate_id,
                AffiliateCommission.status == "paid",
            )
        )
        total_earned = earnings_res.scalar() or 0.0

        payouts_res = await self.db.execute(
            select(func.sum(AffiliatePayout.amount)).filter(
                AffiliatePayout.affiliate_id == affiliate_id,
                AffiliatePayout.status != "cancelled",
            )
        )
        total_paid_out = payouts_res.scalar() or 0.0

        available_balance = total_earned - total_paid_out
        if amount > available_balance:
            raise ValueError(
                f"Insufficient balance. Available: {available_balance:.2f}, Requested: {amount:.2f}"
            )

        payout = AffiliatePayout(
            affiliate_id=affiliate_id,
            amount=amount,
            status="pending",
        )
        self.db.add(payout)
        await self.db.flush()
        return payout

    async def process_payout_status(self, payout_id: int, approve: bool) -> bool:
        """Mark a pending payout request as completed or cancelled."""
        payout = await self.db.get(AffiliatePayout, payout_id)
        if not payout or payout.status != "pending":
            return False

        if approve:
            payout.status = "completed"
            payout.paid_at = datetime.now(timezone.utc)
        else:
            payout.status = "cancelled"

        await self.db.flush()
        return True
