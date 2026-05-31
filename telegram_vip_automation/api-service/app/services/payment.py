import uuid
import logging
from datetime import datetime, timedelta
from typing import Tuple, Optional
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.user import User
from app.models.payment import Payment
from app.models.subscription import Subscription
from app.models.invite import InviteLog
from app.repositories.user import UserRepository
from app.repositories.package import PackageRepository
from app.repositories.payment import PaymentRepository
from app.repositories.subscription import SubscriptionRepository
from app.repositories.channel import TelegramChannelRepository
from app.repositories.invite import InviteLogRepository
from app.services.telegram import TelegramService
from app.core.config import settings

logger = logging.getLogger(__name__)


class PaymentService:
    def __init__(self, db: AsyncSession, telegram_service: TelegramService):
        self.db = db
        self.tg_service = telegram_service
        self.user_repo = UserRepository(db)
        self.pkg_repo = PackageRepository(db)
        self.pay_repo = PaymentRepository(db)
        self.sub_repo = SubscriptionRepository(db)
        self.chan_repo = TelegramChannelRepository(db)
        self.invite_repo = InviteLogRepository(db)

    async def create_mock_payment(
        self,
        telegram_id: int,
        package_id: int,
        idempotency_key: str,
        username: str = "",
        first_name: str = "",
        last_name: str = "",
    ) -> Payment:
        """Create a pending mock payment with idempotency support.

        Idempotency rules:
            - Same key + pending  → return existing (idempotent retry)
            - Same key + paid     → raise (already completed)
            - Same key + failed   → allow new payment (retry flow)
        """
        existing = await self.pay_repo.get_by_idempotency_key(idempotency_key)
        if existing:
            if existing.status == "pending":
                return existing
            if existing.status == "paid":
                raise ValueError("Payment already completed for this idempotency key")
            if existing.status in ("processing",):
                raise ValueError("Payment is currently being processed")
            # status == 'failed' or 'refunded' → allow retry below

        # Ensure user exists
        user_is_new = False
        user = await self.user_repo.get_by_telegram_id(telegram_id)
        if not user:
            user_is_new = True
            user = User(
                telegram_id=telegram_id,
                username=username,
                first_name=first_name,
                last_name=last_name,
            )
            await self.user_repo.create(user)
            # Emit user_registered event
            try:
                from app.services.event import EventService
                event_service = EventService(self.db)
                await event_service.log_event(
                    event_type="user_registered",
                    user_id=user.id,
                    payload_json={"telegram_id": telegram_id, "username": username},
                    source_service="api"
                )
            except Exception as e:
                logger.error(f"Failed to log user_registered event: {e}")
        else:
            user.username = username
            user.first_name = first_name
            user.last_name = last_name
            await self.user_repo.update(user)

        # Fetch package
        package = await self.pkg_repo.get(package_id)
        if not package or not package.is_active:
            raise ValueError(f"Active Package ID {package_id} not found")

        # Create payment record
        provider_tx_id = f"mock_tx_{uuid.uuid4().hex[:12]}"
        payment = Payment(
            user_id=user.id,
            package_id=package.id,
            amount=package.price,
            currency="TRY",
            status="pending",
            provider_tx_id=provider_tx_id,
            idempotency_key=idempotency_key,
        )
        await self.pay_repo.create(payment)

        # Emit payment_created event
        try:
            from app.services.event import EventService
            event_service = EventService(self.db)
            await event_service.log_event(
                event_type="payment_created",
                user_id=user.id,
                payload_json={"payment_id": payment.id, "amount": payment.amount, "package_id": payment.package_id, "idempotency_key": payment.idempotency_key},
                source_service="api"
            )
        except Exception as e:
            logger.error(f"Failed to log payment_created event: {e}")

        return payment

    async def confirm_payment(self, provider_tx_id: str) -> Tuple[Payment, Subscription, str]:
        """Confirm a payment with two-phase state transition and idempotent activation.

        State machine: pending → processing → paid
        Uses SELECT FOR UPDATE to prevent concurrent processing.
        """
        # Lock row for exclusive processing
        payment = await self.pay_repo.get_by_provider_tx_id_for_update(provider_tx_id)
        if not payment:
            raise ValueError(f"Payment with transaction ID {provider_tx_id} not found")

        # Idempotent: already paid → return existing subscription
        if payment.status == "paid":
            package = await self.pkg_repo.get(payment.package_id)
            sub = await self.sub_repo.get_active_by_user_and_product(payment.user_id, package.product_id)
            invites = await self.invite_repo.list_unused_by_user(payment.user_id)
            invite_link = invites[0].invite_link if invites else ""
            return payment, sub, invite_link

        # Reject concurrent processing
        if payment.status == "processing":
            raise ValueError("Payment is currently being processed by another request")

        if payment.status not in ("pending",):
            raise ValueError(f"Payment cannot be confirmed from '{payment.status}' status")

        # Phase 1: pending → processing
        payment.transition_to("processing")
        await self.pay_repo.update(payment)
        await self.db.flush()

        try:
            # Phase 2: processing → paid
            payment.transition_to("paid")
            await self.pay_repo.update(payment)

            # Get package and user details
            package = await self.pkg_repo.get(payment.package_id)
            user = await self.user_repo.get(payment.user_id)

            # Emit payment_completed event
            try:
                from app.services.event import EventService
                event_service = EventService(self.db)
                await event_service.log_event(
                    event_type="payment_completed",
                    user_id=user.id,
                    payload_json={"payment_id": payment.id, "amount": payment.amount, "provider_tx_id": provider_tx_id},
                    source_service="api"
                )
            except Exception as e:
                logger.error(f"Failed to log payment_completed event: {e}")

            # Determine subscription period
            now = datetime.now()
            existing_sub = await self.sub_repo.get_active_by_user_and_product(user.id, package.product_id)

            is_renewal = False
            if existing_sub and existing_sub.end_date > now:
                is_renewal = True
                start_date = existing_sub.start_date
                end_date = existing_sub.end_date + timedelta(days=package.duration_days)
                existing_sub.end_date = end_date
                existing_sub.package_id = package.id
                subscription = await self.sub_repo.update(existing_sub)
            else:
                if existing_sub:
                    existing_sub.is_active = False
                    await self.sub_repo.update(existing_sub)

                start_date = now
                end_date = now + timedelta(days=package.duration_days)
                subscription = Subscription(
                    user_id=user.id,
                    package_id=package.id,
                    product_id=package.product_id,
                    start_date=start_date,
                    end_date=end_date,
                    is_active=True,
                )
                await self.sub_repo.create(subscription)

            # Emit subscription event
            try:
                from app.services.event import EventService
                event_service = EventService(self.db)
                event_type = "subscription_renewed" if is_renewal else "subscription_started"
                await event_service.log_event(
                    event_type=event_type,
                    user_id=user.id,
                    payload_json={"subscription_id": subscription.id, "product_id": subscription.product_id, "end_date": subscription.end_date.isoformat()},
                    source_service="api"
                )
            except Exception as e:
                logger.error(f"Failed to log subscription event: {e}")

            # Generate invite links for all channels in the product
            from app.models.product import ProductChannel
            from app.models.channel import TelegramChannel
            from sqlalchemy import select
            channels_res = await self.db.execute(
                select(TelegramChannel)
                .join(ProductChannel, ProductChannel.channel_id == TelegramChannel.id)
                .filter(ProductChannel.product_id == package.product_id)
            )
            prod_channels = channels_res.scalars().all()

            invite_link = ""
            expire_date = now + timedelta(hours=24)

            if prod_channels:
                for chan in prod_channels:
                    link = await self.tg_service.create_single_use_invite(
                        chat_id=chan.telegram_id,
                        name=f"User {user.telegram_id} VIP Access",
                        expire_date=expire_date,
                    )
                    if not link:
                        link = f"https://t.me/placeholder_link_token_{uuid.uuid4().hex[:8]}"
                    
                    if not invite_link:
                        invite_link = link
                    
                    invite_log = InviteLog(
                        user_id=user.id,
                        channel_id=chan.id,
                        invite_link=link,
                        is_used=False,
                        expire_date=expire_date,
                    )
                    await self.invite_repo.create(invite_log)
            else:
                # Fallback to default legacy channel
                channel = await self.chan_repo.get_by_telegram_id(settings.VIP_CHANNEL_ID)
                if not channel:
                    channel = await self.chan_repo.create(
                        self.chan_repo.model(
                            telegram_id=settings.VIP_CHANNEL_ID,
                            title="VIP Premium Channel",
                        )
                    )
                link = await self.tg_service.create_single_use_invite(
                    chat_id=settings.VIP_CHANNEL_ID,
                    name=f"User {user.telegram_id} VIP Access",
                    expire_date=expire_date,
                )
                if not link:
                    link = f"https://t.me/placeholder_link_token_{uuid.uuid4().hex[:8]}"
                invite_link = link
                
                invite_log = InviteLog(
                    user_id=user.id,
                    channel_id=channel.id,
                    invite_link=link,
                    is_used=False,
                    expire_date=expire_date,
                )
                await self.invite_repo.create(invite_log)

            # Trigger coupon redemption
            if payment.coupon_id:
                try:
                    from app.services.coupon import CouponService
                    from app.models.coupon import Coupon
                    coupon_service = CouponService(self.db)
                    coupon_res = await self.db.execute(
                        select(Coupon).filter(Coupon.id == payment.coupon_id)
                    )
                    coupon = coupon_res.scalars().first()
                    if coupon:
                        await coupon_service.redeem_coupon(coupon.code, user.id, payment.id)

                        # Emit coupon_redeemed event
                        try:
                            from app.services.event import EventService
                            event_service = EventService(self.db)
                            await event_service.log_event(
                                event_type="coupon_redeemed",
                                user_id=user.id,
                                payload_json={"coupon_id": coupon.id, "code": coupon.code, "payment_id": payment.id},
                                source_service="api"
                            )
                        except Exception as e:
                            logger.error(f"Failed to log coupon_redeemed event: {e}")
                except Exception as e:
                    logger.error(f"Error redeeming coupon: {e}")

            # Trigger referral processing
            try:
                from app.services.referral import ReferralService
                ref_service = ReferralService(self.db)
                await ref_service.process_referral_on_payment(user.id)
            except Exception as e:
                logger.error(f"Error processing referral reward: {e}")

            # Trigger affiliate commission processing
            try:
                from app.services.affiliate import AffiliateService
                aff_service = AffiliateService(self.db)
                await aff_service.process_commission_on_payment(payment.id)
            except Exception as e:
                logger.error(f"Error processing affiliate commission: {e}")

            # Mark sent campaign executions as converted
            try:
                from app.models.campaign import CampaignExecution
                exec_res = await self.db.execute(
                    select(CampaignExecution).filter(
                        CampaignExecution.user_id == user.id,
                        CampaignExecution.status == "sent"
                    )
                )
                for ex in exec_res.scalars().all():
                    ex.status = "converted"
                    ex.converted_at = datetime.now(timezone.utc)
            except Exception as e:
                logger.error(f"Failed to update campaign execution conversion status: {e}")

            return payment, subscription, invite_link

        except Exception:
            # If anything fails after processing, revert to failed
            payment.status = "failed"
            await self.pay_repo.update(payment)
            
            # Emit payment_failed event
            try:
                from app.services.event import EventService
                event_service = EventService(self.db)
                await event_service.log_event(
                    event_type="payment_failed",
                    user_id=payment.user_id if payment else None,
                    payload_json={"payment_id": payment.id if payment else None, "provider_tx_id": provider_tx_id},
                    source_service="api"
                )
            except Exception as e:
                logger.error(f"Failed to log payment_failed event: {e}")
            raise
