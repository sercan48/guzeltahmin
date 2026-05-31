import logging
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Any, Optional

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy.orm import selectinload

from app.models.user import User
from app.models.payment import Payment
from app.models.subscription import Subscription
from app.models.product import ProductPackage, ProductChannel
from app.models.channel import TelegramChannel
from app.models.audit_log import AdminAuditLog
from app.services.telegram import TelegramService

logger = logging.getLogger(__name__)


class ReconciliationService:
    def __init__(self, db: AsyncSession, telegram_service: Optional[TelegramService] = None):
        self.db = db
        self.tg = telegram_service or TelegramService()

    async def run_reconciliation(self) -> Dict[str, int]:
        """Perform system reconciliation, auto-repairing data drifts and return stats."""
        stats = {
            "subscriptions_created": 0,
            "reinvites_sent": 0,
            "users_kicked": 0
        }
        
        now = datetime.now(timezone.utc)
        
        # We need a Redis client to increment metrics. Let's lazily import redis
        import redis.asyncio as aioredis
        from app.core.config import settings
        redis = None
        if settings.REDIS_URL:
            try:
                redis = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
            except Exception as rex:
                logger.warning(f"Redis unavailable for reconciliation metrics: {rex}")

        # ── 1. Ödeme Kontrolü (Payment vs Subscription) ───────────────────────────
        try:
            # Fetch all paid payments
            pay_stmt = select(Payment).filter(Payment.status == "paid").order_by(Payment.created_at.desc())
            pay_res = await self.db.execute(pay_stmt)
            payments = pay_res.scalars().all()
            
            for pay in payments:
                # Check if there is an active subscription covering this product/package
                sub_stmt = select(Subscription).filter(
                    Subscription.user_id == pay.user_id,
                    Subscription.package_id == pay.package_id,
                    Subscription.is_active == True,
                    Subscription.end_date > pay.created_at
                )
                sub_res = await self.db.execute(sub_stmt)
                existing_sub = sub_res.scalars().first()
                
                if not existing_sub:
                    logger.warning(f"Reconciliation: Payment {pay.id} has no active subscription! Auto-repairing...")
                    
                    # Fetch package to get duration
                    pkg_stmt = select(ProductPackage).filter(ProductPackage.id == pay.package_id)
                    pkg_res = await self.db.execute(pkg_stmt)
                    package = pkg_res.scalars().first()
                    
                    if not package:
                        logger.error(f"Reconciliation: Package {pay.package_id} not found for payment {pay.id}. Skipping sub creation.")
                        continue
                        
                    start_date = pay.created_at
                    end_date = start_date + timedelta(days=package.duration_days)
                    
                    # Create subscription
                    new_sub = Subscription(
                        user_id=pay.user_id,
                        product_id=package.product_id,
                        package_id=package.id,
                        start_date=start_date,
                        end_date=end_date,
                        is_active=True
                    )
                    self.db.add(new_sub)
                    await self.db.flush()
                    
                    # Log to audit trail
                    audit = AdminAuditLog(
                        admin_id=1,  # Default system admin ID
                        action="reconciliation_auto_create_sub",
                        target_type="subscription",
                        target_id=str(new_sub.id),
                        details=f"Auto-created subscription for payment {pay.id} (user {pay.user_id})."
                    )
                    self.db.add(audit)
                    
                    stats["subscriptions_created"] += 1
                    if redis:
                        await redis.incr("metrics:counter:reconciliation_fix_count:sub_created")
                        
            await self.db.commit()
        except Exception as e_pay:
            logger.error(f"Error in reconciliation payment check: {e_pay}")
            await self.db.rollback()

        # ── 2. Abonelik Kontrolü (Subscription vs Channel Membership) ──────────────
        try:
            # Fetch all active subscriptions
            sub_stmt = select(Subscription, User).join(User, Subscription.user_id == User.id).filter(
                Subscription.is_active == True,
                Subscription.end_date > now
            )
            sub_res = await self.db.execute(sub_stmt)
            active_subs_users = sub_res.all()
            
            for sub, user in active_subs_users:
                # Fetch channels linked to this product
                chan_stmt = select(TelegramChannel).join(
                    ProductChannel, ProductChannel.channel_id == TelegramChannel.id
                ).filter(ProductChannel.product_id == sub.product_id)
                
                chan_res = await self.db.execute(chan_stmt)
                channels = chan_res.scalars().all()
                
                for channel in channels:
                    in_chat = await self.tg.is_user_in_chat(str(channel.telegram_id), user.telegram_id)
                    if not in_chat:
                        logger.warning(
                            f"Reconciliation: Active subscriber {user.telegram_id} "
                            f"(Sub: {sub.id}) is not in channel {channel.telegram_id}! Auto-repairing..."
                        )
                        
                        # Generate new invite link
                        invite_link = await self.tg.create_single_use_invite(
                            chat_id=str(channel.telegram_id),
                            name=f"Re-invite {user.username or user.telegram_id}",
                            expire_date=sub.end_date
                        )
                        
                        if invite_link:
                            try:
                                await self.tg.bot.send_message(
                                    chat_id=user.telegram_id,
                                    text=(
                                        "🔔 **VIP Kanal Üyeliği Hatırlatması**\n\n"
                                        "Aktif bir VIP aboneliğiniz bulunmasına rağmen kanalda olmadığınızı tespit ettik.\n"
                                        "Tahminlerimizi kaçırmamak için aşağıdaki bağlantıyı kullanarak kanala tekrar katılabilirsiniz:\n\n"
                                        f"👉 {invite_link}"
                                    ),
                                    parse_mode="Markdown"
                                )
                                logger.info(f"Reconciliation: Sent invite link to user {user.telegram_id}")
                                
                                # Log audit
                                audit = AdminAuditLog(
                                    admin_id=1,
                                    action="reconciliation_reinvite_user",
                                    target_type="user",
                                    target_id=str(user.id),
                                    details=f"User not in channel {channel.telegram_id}, sent re-invite link via DM."
                                )
                                self.db.add(audit)
                                stats["reinvites_sent"] += 1
                                if redis:
                                    await redis.incr("metrics:counter:reconciliation_fix_count:reinvite_sent")
                            except Exception as e_msg:
                                logger.warning(f"Reconciliation: Could not send Telegram DM to user {user.telegram_id}: {e_msg}")
            
            await self.db.commit()
        except Exception as e_sub:
            logger.error(f"Error in reconciliation active sub check: {e_sub}")
            await self.db.rollback()

        # ── 3. Yetkisiz Erişim Kontrolü (Expired vs Channel Membership) ─────────────
        try:
            # Fetch expired subscriptions (or users with expired/cancelled subs)
            # Find users who have expired subscriptions and NO active subscriptions for the same product
            # First, fetch all expired/cancelled subscriptions
            expired_stmt = select(Subscription, User).join(User, Subscription.user_id == User.id).filter(
                (Subscription.is_active == False) | (Subscription.end_date <= now)
            )
            expired_res = await self.db.execute(expired_stmt)
            expired_subs_users = expired_res.all()
            
            for sub, user in expired_subs_users:
                # Verify they don't have another active subscription for this product
                active_stmt = select(Subscription).filter(
                    Subscription.user_id == user.id,
                    Subscription.product_id == sub.product_id,
                    Subscription.is_active == True,
                    Subscription.end_date > now
                )
                active_res = await self.db.execute(active_stmt)
                has_active = active_res.scalars().first()
                
                if has_active:
                    # They have another active subscription, they are authorized!
                    continue
                    
                # Fetch channels linked to this product
                chan_stmt = select(TelegramChannel).join(
                    ProductChannel, ProductChannel.channel_id == TelegramChannel.id
                ).filter(ProductChannel.product_id == sub.product_id)
                
                chan_res = await self.db.execute(chan_stmt)
                channels = chan_res.scalars().all()
                
                for channel in channels:
                    in_chat = await self.tg.is_user_in_chat(str(channel.telegram_id), user.telegram_id)
                    if in_chat:
                        logger.warning(
                            f"Reconciliation: Expired subscriber {user.telegram_id} "
                            f"(Sub: {sub.id}) is still in channel {channel.telegram_id}! Auto-repairing (Kick)..."
                        )
                        
                        # Kick user
                        kicked = await self.tg.kick_user(str(channel.telegram_id), user.telegram_id)
                        if kicked:
                            # Send message
                            try:
                                await self.tg.bot.send_message(
                                    chat_id=user.telegram_id,
                                    text=(
                                        "⚠️ **VIP Kanal Erişimi Sona Erdi!**\n\n"
                                        "Abonelik süreniz dolduğu için kanaldan otomatik olarak çıkarıldınız.\n"
                                        "Tekrar katılmak için `/start` komutu ile yeni bir paket satın alabilirsiniz."
                                    ),
                                    parse_mode="Markdown"
                                )
                            except Exception:
                                pass
                                
                            # Log audit
                            audit = AdminAuditLog(
                                admin_id=1,
                                action="reconciliation_kick_user",
                                target_type="user",
                                target_id=str(user.id),
                                details=f"Kicked user from channel {channel.telegram_id} due to expired subscription {sub.id}."
                            )
                            self.db.add(audit)
                            stats["users_kicked"] += 1
                            if redis:
                                await redis.incr("metrics:counter:reconciliation_fix_count:user_kicked")
            
            await self.db.commit()
        except Exception as e_exp:
            logger.error(f"Error in reconciliation expired sub check: {e_exp}")
            await self.db.rollback()
            
        if redis:
            await redis.aclose()
            
        await self.tg.close()
        logger.info(f"Reconciliation run complete. Stats: {stats}")
        return stats
