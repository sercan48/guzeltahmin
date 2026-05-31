import logging
from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.models.user import User
from app.models.product import Product, ProductPackage
from app.models.channel import TelegramChannel
from app.models.subscription import Subscription
from app.repositories.subscription import SubscriptionRepository
from app.repositories.channel import TelegramChannelRepository
from app.core.config import settings

logger = logging.getLogger(__name__)


class TrialService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.sub_repo = SubscriptionRepository(db)
        self.chan_repo = TelegramChannelRepository(db)

    async def _get_or_create_trial_package(self) -> ProductPackage:
        """Get or create the 3-day free trial package in the database."""
        result = await self.db.execute(
            select(ProductPackage).filter(ProductPackage.name == "Free Trial")
        )
        package = result.scalars().first()
        if not package:
            # Ensure a default product exists
            prod_res = await self.db.execute(select(Product).limit(1))
            product = prod_res.scalars().first()
            if not product:
                product = Product(
                    name="Default VIP Product",
                    description="Default VIP product created automatically",
                    is_active=True
                )
                self.db.add(product)
                await self.db.flush()

            package = ProductPackage(
                product_id=product.id,
                name="Free Trial",
                description="3-Day Free Trial VIP Membership",
                price=0.0,
                duration_days=3,
                is_active=False,  # Invisible in normal package list
            )
            self.db.add(package)
            await self.db.flush()
        return package

    async def claim_free_trial(self, telegram_id: int) -> Tuple[bool, str, Optional[Subscription], str]:
        """Process a 3-day free trial claim for a user.

        Requires user.trial_used == False.
        """
        # Fetch user
        result = await self.db.execute(
            select(User).filter(User.telegram_id == telegram_id)
        )
        user = result.scalars().first()
        if not user:
            return False, "User not registered in the system", None, ""

        if user.trial_used:
            return False, "You have already claimed your free trial", None, ""

        # Fetch/Create trial package
        package = await self._get_or_create_trial_package()

        # Deactivate any currently active subscriptions for this user and product
        existing_sub = await self.sub_repo.get_active_by_user_and_product(user.id, package.product_id)
        if existing_sub:
            existing_sub.is_active = False
            await self.sub_repo.update(existing_sub)

        # Create 3-day subscription
        now = datetime.now(timezone.utc)
        sub = Subscription(
            user_id=user.id,
            package_id=package.id,
            product_id=package.product_id,
            start_date=now,
            end_date=now + timedelta(days=3),
            is_active=True,
        )
        
        # Mark trial as used
        user.trial_used = True
        
        self.db.add(sub)
        await self.db.flush()

        # Emit trial_claimed event
        try:
            from app.services.event import EventService
            event_service = EventService(self.db)
            await event_service.log_event(
                event_type="trial_claimed",
                user_id=user.id,
                payload_json={"subscription_id": sub.id, "product_id": sub.product_id, "duration_days": 3},
                source_service="api"
            )
        except Exception as e:
            logger.error(f"Failed to log trial_claimed event: {e}")

        # Generate single-use invite link for product channels or fallback
        from app.services.telegram import TelegramService
        from app.models.invite import InviteLog
        from app.models.product import ProductChannel
        tg_service = TelegramService()
        invite_link = ""

        try:
            expire_date = now + timedelta(hours=24)
            channels_res = await self.db.execute(
                select(TelegramChannel)
                .join(ProductChannel, ProductChannel.channel_id == TelegramChannel.id)
                .filter(ProductChannel.product_id == package.product_id)
            )
            prod_channels = channels_res.scalars().all()

            if prod_channels:
                for chan in prod_channels:
                    link = await tg_service.create_single_use_invite(
                        chat_id=chan.telegram_id,
                        name=f"User {telegram_id} Trial Access",
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
                    self.db.add(invite_log)
            else:
                # Fallback to legacy VIP channel
                channel = await self.chan_repo.get_by_telegram_id(settings.VIP_CHANNEL_ID)
                if not channel:
                    channel = await self.chan_repo.create(
                        self.chan_repo.model(
                            telegram_id=settings.VIP_CHANNEL_ID,
                            title="VIP Premium Channel",
                        )
                    )
                link = await tg_service.create_single_use_invite(
                    chat_id=settings.VIP_CHANNEL_ID,
                    name=f"User {telegram_id} Trial Access",
                    expire_date=expire_date,
                )
                if not link:
                    link = f"https://t.me/placeholder_link_token_{uuid.uuid4().hex[:8]}"
                invite_link = link

                invite_log = InviteLog(
                    user_id=user.id,
                    channel_id=channel.id,
                    invite_link=invite_link,
                    is_used=False,
                    expire_date=expire_date,
                )
                self.db.add(invite_log)
                
            await self.db.flush()
        except Exception as e:
            logger.error(f"Failed to generate trial invite link: {e}")
        finally:
            await tg_service.close()
        
        logger.info(
            "claimed_free_trial",
            extra={"user_id": user.id, "telegram_id": telegram_id, "subscription_id": sub.id},
        )
        return True, "3-day free trial activated successfully", sub, invite_link
