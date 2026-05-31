import logging
from datetime import datetime, timezone, timedelta
from typing import Optional, List
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.models.admin import Admin
from app.models.audit_log import AdminAuditLog
from app.models.user import User
from app.models.subscription import Subscription
from app.core.admin_auth import hash_password

logger = logging.getLogger(__name__)


class AdminService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def create_initial_admin(self) -> None:
        """Create initial admin user if it does not already exist."""
        result = await self.db.execute(
            select(Admin).filter(Admin.username == "admin")
        )
        existing = result.scalars().first()
        if not existing:
            logger.info("Seeding initial default admin...")
            admin = Admin(
                username="admin",
                password_hash=hash_password("adminpassword"),
                role="admin",
                is_active=True,
            )
            self.db.add(admin)
            await self.db.flush()
            logger.info("Default admin created successfully with username 'admin' and password 'adminpassword'.")
        else:
            logger.debug("Admin user already exists, skipping seed.")

    async def log_audit_action(
        self,
        admin_id: int,
        action: str,
        target_type: str,
        target_id: str,
        details: Optional[str] = None,
        ip_address: Optional[str] = None
    ) -> AdminAuditLog:
        """Helper to create and insert an admin audit log."""
        audit_log = AdminAuditLog(
            admin_id=admin_id,
            action=action,
            target_type=target_type,
            target_id=target_id,
            details=details,
            ip_address=ip_address
        )
        self.db.add(audit_log)
        await self.db.flush()
        return audit_log

    async def suspend_user(self, user_id: int, admin_id: int, ip_address: Optional[str] = None) -> bool:
        """Suspend user and deactivate all active subscriptions."""
        result = await self.db.execute(select(User).filter(User.id == user_id))
        user = result.scalars().first()
        if not user:
            return False
        user.is_suspended = True
        
        # Deactivate all active subscriptions for this user
        sub_res = await self.db.execute(
            select(Subscription).filter(Subscription.user_id == user_id, Subscription.is_active == True)
        )
        for sub in sub_res.scalars().all():
            sub.is_active = False
            
        await self.db.flush()
        await self.log_audit_action(
            admin_id=admin_id,
            action="suspend_user",
            target_type="user",
            target_id=str(user_id),
            details="Suspended user and deactivated all active subscriptions",
            ip_address=ip_address
        )
        return True

    async def unsuspend_user(self, user_id: int, admin_id: int, ip_address: Optional[str] = None) -> bool:
        """Unsuspend user, restoring access."""
        result = await self.db.execute(select(User).filter(User.id == user_id))
        user = result.scalars().first()
        if not user:
            return False
        user.is_suspended = False
        await self.db.flush()
        await self.log_audit_action(
            admin_id=admin_id,
            action="unsuspend_user",
            target_type="user",
            target_id=str(user_id),
            details="Unsuspended user",
            ip_address=ip_address
        )
        return True

    async def grant_days(self, user_id: int, days: int, product_id: int, admin_id: int, ip_address: Optional[str] = None) -> bool:
        """Grant VIP days to a user for a specific product."""
        result = await self.db.execute(select(User).filter(User.id == user_id))
        user = result.scalars().first()
        if not user:
            return False
            
        now = datetime.now(timezone.utc)
        
        # Check active subscription for this user and product
        sub_res = await self.db.execute(
            select(Subscription).filter(
                Subscription.user_id == user_id,
                Subscription.product_id == product_id,
                Subscription.is_active == True,
                Subscription.end_date > now
            )
        )
        existing_sub = sub_res.scalars().first()
        
        if existing_sub:
            existing_sub.end_date += timedelta(days=days)
        else:
            # Find the first package of this product to link to subscription
            from app.models.product import ProductPackage
            pkg_res = await self.db.execute(
                select(ProductPackage).filter(ProductPackage.product_id == product_id).limit(1)
            )
            package = pkg_res.scalars().first()
            if not package:
                return False
                
            new_sub = Subscription(
                user_id=user_id,
                product_id=product_id,
                package_id=package.id,
                start_date=now,
                end_date=now + timedelta(days=days),
                is_active=True
            )
            self.db.add(new_sub)
            
        await self.db.flush()
        await self.log_audit_action(
            admin_id=admin_id,
            action="grant_days",
            target_type="user",
            target_id=str(user_id),
            details=f"Granted {days} days for product {product_id}",
            ip_address=ip_address
        )
        return True

    async def extend_subscription(self, subscription_id: int, days: int, admin_id: int, ip_address: Optional[str] = None) -> bool:
        """Extend an existing subscription by a number of days."""
        result = await self.db.execute(select(Subscription).filter(Subscription.id == subscription_id))
        sub = result.scalars().first()
        if not sub:
            return False
        sub.end_date += timedelta(days=days)
        sub.is_active = True
        await self.db.flush()
        await self.log_audit_action(
            admin_id=admin_id,
            action="extend_subscription",
            target_type="subscription",
            target_id=str(subscription_id),
            details=f"Extended subscription end date by {days} days",
            ip_address=ip_address
        )
        return True

    async def cancel_subscription(self, subscription_id: int, admin_id: int, ip_address: Optional[str] = None) -> bool:
        """Cancel subscription immediately."""
        result = await self.db.execute(select(Subscription).filter(Subscription.id == subscription_id))
        sub = result.scalars().first()
        if not sub:
            return False
        sub.is_active = False
        await self.db.flush()
        await self.log_audit_action(
            admin_id=admin_id,
            action="cancel_subscription",
            target_type="subscription",
            target_id=str(subscription_id),
            details="Cancelled subscription",
            ip_address=ip_address
        )
        return True
