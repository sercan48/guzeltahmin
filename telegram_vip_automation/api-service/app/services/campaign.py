import logging
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Any
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy import func

from app.models.subscription import Subscription
from app.models.user import User
from app.models.notification import NotificationLog

logger = logging.getLogger(__name__)


class CampaignService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_pending_notifications(self) -> List[Dict[str, Any]]:
        """Identify users who need to be sent renewal notifications.

        Scans:
        - T-7 days: Subscription ends in 7 days.
        - T-3 days: Subscription ends in 3 days.
        - T-1 day: Subscription ends in 1 day.
        - T+1 day: Subscription ended 1 day ago (Grace Period).
        - T+7 days: Subscription ended 7 days ago (Win-back).
        """
        now = datetime.now(timezone.utc)
        today = now.date()

        pending_notifications = []

        # We define a mapping of notification types to their target end_date offset (in days relative to today)
        # e.g., for T-7, the subscription ends 7 days from now (offset = +7)
        # for T+1, the subscription ended 1 day ago (offset = -1)
        campaign_rules = {
            "T-7": 7,
            "T-3": 3,
            "T-1": 1,
            "T+1": -1,
            "T+7": -7,
        }

        # Select all subscriptions that are active (or were active but expired recently for winback)
        result = await self.db.execute(
            select(Subscription, User)
            .join(User, Subscription.user_id == User.id)
        )
        subs_users = result.all()

        for sub, user in subs_users:
            sub_end_date = sub.end_date.date()
            
            for notif_type, offset_days in campaign_rules.items():
                target_date = today + timedelta(days=offset_days)
                
                # Check if the subscription end date falls on the target date
                if sub_end_date == target_date:
                    # Check if we already sent this notification
                    log_res = await self.db.execute(
                        select(NotificationLog).filter(
                            NotificationLog.subscription_id == sub.id,
                            NotificationLog.notification_type == notif_type,
                        )
                    )
                    already_sent = log_res.scalars().first()
                    if not already_sent:
                        message = self._get_message_for_type(notif_type)
                        pending_notifications.append({
                            "subscription_id": sub.id,
                            "user_id": user.id,
                            "telegram_id": user.telegram_id,
                            "notification_type": notif_type,
                            "message": message,
                        })

        return pending_notifications

    def _get_message_for_type(self, notif_type: str) -> str:
        if notif_type == "T-7":
            return (
                "📅 **VIP Üyeliğiniz Hakkında**\n\n"
                "VIP aboneliğinizin sona ermesine **7 gün** kaldı. "
                "Kesintisiz hizmet almaya devam etmek için şimdiden yenileyebilirsiniz!"
            )
        elif notif_type == "T-3":
            return (
                "🔥 **VIP Yenilemeye Özel Fırsat!**\n\n"
                "Aboneliğinizin bitmesine **3 gün** kaldı. "
                "Hemen yenilemek için size özel **%15 indirim** kuponu: `RENEW15`\n\n"
                "Bu kuponu ödeme adımında kullanabilirsiniz."
            )
        elif notif_type == "T-1":
            return (
                "⚠️ **Son Gün!**\n\n"
                "VIP aboneliğiniz **yarın sona eriyor**. "
                "Yarınki kazandıran tahminleri kaçırmamak için üyeliğinizi yenilemeyi unutmayın!"
            )
        elif notif_type == "T+1":
            return (
                "🎁 **Size Özel 24 Saatlik Müsamaha (Grace Period)!**\n\n"
                "VIP üyeliğiniz dün sona erdi. Ancak sizin için VIP kanal erişimini **24 saat daha açık tuttuk**!\n"
                "Tahminlerimizi kaçırmadan hemen yenilemek için `/start` yazıp paketinizi seçebilirsiniz."
            )
        elif notif_type == "T+7":
            return (
                "👋 **Sizi Özledik!**\n\n"
                "VIP grubumuzdan ayrılalı 7 gün oldu. Geri dönmek isterseniz size özel **%25 indirim** kuponu hazırladık: `WELCOMEBACK25`\n\n"
                "Yeniden aramıza katılmak için sabırsızlanıyoruz!"
            )
        return ""

    async def log_sent_notification(self, subscription_id: int, user_id: int, notification_type: str) -> NotificationLog:
        """Create a NotificationLog entry to prevent duplicates."""
        log = NotificationLog(
            subscription_id=subscription_id,
            user_id=user_id,
            notification_type=notification_type,
            status="sent",
        )
        self.db.add(log)
        await self.db.flush()
        return log

    async def trigger_campaigns_on_event(self, event) -> None:
        """Evaluate campaign rules when an event is logged."""
        from app.models.campaign import Campaign, CampaignRule, CampaignExecution
        
        # Check active campaign rules triggered by this event type
        rule_res = await self.db.execute(
            select(CampaignRule)
            .join(Campaign, CampaignRule.campaign_id == Campaign.id)
            .filter(CampaignRule.trigger_event == event.event_type, Campaign.is_active == True)
        )
        rules = rule_res.scalars().all()
        
        for rule in rules:
            if not event.user_id:
                continue
                
            # 1. Prevent duplicate executions for the same user, campaign, and triggered event
            dup_res = await self.db.execute(
                select(func.count(CampaignExecution.id)).filter(
                    CampaignExecution.campaign_id == rule.campaign_id,
                    CampaignExecution.user_id == event.user_id,
                    CampaignExecution.triggered_by_event_id == event.id
                )
            )
            if (dup_res.scalar() or 0) > 0:
                continue

            # 2. Cooldown Period: One campaign execution per user in 48 hours
            cooldown_limit = datetime.now(timezone.utc) - timedelta(hours=48)
            cooldown_res = await self.db.execute(
                select(func.count(CampaignExecution.id)).filter(
                    CampaignExecution.campaign_id == rule.campaign_id,
                    CampaignExecution.user_id == event.user_id,
                    CampaignExecution.executed_at >= cooldown_limit
                )
            )
            if (cooldown_res.scalar() or 0) > 0:
                logger.info(f"Campaign {rule.campaign_id} trigger skipped for user {event.user_id} due to 48h cooldown.")
                continue
                
            # Create campaign execution record
            now = datetime.now(timezone.utc)
            execute_at = now + timedelta(hours=rule.delay_hours)
            
            execution = CampaignExecution(
                campaign_id=rule.campaign_id,
                user_id=event.user_id,
                status="pending",
                triggered_by_event_id=event.id,
                executed_at=execute_at
            )
            self.db.add(execution)
            
        await self.db.flush()

