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
        self, telegram_id: int, package_id: int, username: str = "", first_name: str = "", last_name: str = ""
    ) -> Payment:
        """Create a new mock payment record in pending state."""
        # 1. Ensure user exists
        user = await self.user_repo.get_by_telegram_id(telegram_id)
        if not user:
            user = User(
                telegram_id=telegram_id,
                username=username,
                first_name=first_name,
                last_name=last_name
            )
            await self.user_repo.create(user)
        else:
            # Update user info if changed
            user.username = username
            user.first_name = first_name
            user.last_name = last_name
            await self.user_repo.update(user)

        # 2. Fetch Package
        package = await self.pkg_repo.get(package_id)
        if not package or not package.is_active:
            raise ValueError(f"Active Package ID {package_id} not found")

        # 3. Create Payment record
        provider_tx_id = f"mock_tx_{uuid.uuid4().hex[:12]}"
        payment = Payment(
            user_id=user.id,
            package_id=package.id,
            amount=package.price,
            currency="TRY",
            status="pending",
            provider_tx_id=provider_tx_id
        )
        await self.pay_repo.create(payment)
        return payment

    async def confirm_payment(self, provider_tx_id: str) -> Tuple[Payment, Subscription, str]:
        """Confirm a payment and activate/extend the subscription, generating an invite link."""
        payment = await self.pay_repo.get_by_provider_tx_id(provider_tx_id)
        if not payment:
            raise ValueError(f"Payment with transaction ID {provider_tx_id} not found")
        
        if payment.status == "paid":
            # Already processed, just return subscription details
            sub = await self.sub_repo.get_active_by_user(payment.user_id)
            invites = await self.invite_repo.list_unused_by_user(payment.user_id)
            invite_link = invites[0].invite_link if invites else ""
            return payment, sub, invite_link

        # Mark payment as paid
        payment.status = "paid"
        await self.pay_repo.update(payment)

        # Get package and user details
        package = await self.pkg_repo.get(payment.package_id)
        user = await self.user_repo.get(payment.user_id)

        # Fetch channel (Ensure we have at least one TelegramChannel mapped to settings.VIP_CHANNEL_ID)
        channel = await self.chan_repo.get_by_telegram_id(settings.VIP_CHANNEL_ID)
        if not channel:
            # Create default channel metadata if not exists
            channel = await self.chan_repo.create(
                self.chan_repo.model(
                    telegram_id=settings.VIP_CHANNEL_ID,
                    title="VIP Premium Channel"
                )
            )

        # Determine subscription period
        now = datetime.now()
        existing_sub = await self.sub_repo.get_active_by_user(user.id)
        
        if existing_sub and existing_sub.end_date > now:
            # Extend existing subscription
            start_date = existing_sub.start_date
            end_date = existing_sub.end_date + timedelta(days=package.duration_days)
            existing_sub.end_date = end_date
            existing_sub.package_id = package.id
            subscription = await self.sub_repo.update(existing_sub)
        else:
            # Deactivate any old subscription
            if existing_sub:
                existing_sub.is_active = False
                await self.sub_repo.update(existing_sub)
            
            # Start fresh subscription
            start_date = now
            end_date = now + timedelta(days=package.duration_days)
            subscription = Subscription(
                user_id=user.id,
                package_id=package.id,
                channel_id=channel.id,
                start_date=start_date,
                end_date=end_date,
                is_active=True
            )
            await self.sub_repo.create(subscription)

        # Generate single-use invite link expiring in 24 hours
        expire_date = now + timedelta(hours=24)
        invite_link = await self.tg_service.create_single_use_invite(
            chat_id=settings.VIP_CHANNEL_ID,
            name=f"User {user.telegram_id} VIP Access",
            expire_date=expire_date
        )
        
        if not invite_link:
            # Fallback placeholder link if Telegram API fails/bot not in channel
            invite_link = f"https://t.me/placeholder_link_token_{uuid.uuid4().hex[:8]}"

        # Save invite log
        invite_log = InviteLog(
            user_id=user.id,
            channel_id=channel.id,
            invite_link=invite_link,
            is_used=False,
            expire_date=expire_date
        )
        await self.invite_repo.create(invite_log)

        return payment, subscription, invite_link
