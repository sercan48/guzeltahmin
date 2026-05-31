"""Subscription management for premium Telegram channel."""

import logging
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


class SubscriptionManager:
    """Manage premium subscribers via DB."""

    def __init__(self, db):
        self.db = db

    def add_subscriber(self, telegram_id: int, username: str = "",
                       full_name: str = "", plan: str = "premium",
                       days: int = 30, added_by: str = "admin") -> bool:
        """Add or reactivate a subscriber."""
        try:
            now = datetime.now()
            end = now + timedelta(days=days)

            existing = self.get_subscriber(telegram_id)
            if existing:
                self.db.execute("""
                    UPDATE subscribers
                    SET plan = ?, start_date = ?, end_date = ?,
                        is_active = 1, added_by = ?, username = ?, full_name = ?
                    WHERE telegram_id = ?
                """, (plan, now.isoformat(), end.isoformat(),
                      added_by, username, full_name, telegram_id))
            else:
                self.db.execute("""
                    INSERT INTO subscribers
                    (telegram_id, username, full_name, plan, start_date, end_date, is_active, added_by)
                    VALUES (?, ?, ?, ?, ?, ?, 1, ?)
                """, (telegram_id, username, full_name, plan,
                      now.isoformat(), end.isoformat(), added_by))

            logger.info(f"Subscriber added: {telegram_id} ({plan}, {days} days)")
            return True
        except Exception as e:
            logger.error(f"Failed to add subscriber: {e}")
            return False

    def remove_subscriber(self, telegram_id: int) -> bool:
        try:
            self.db.execute(
                "UPDATE subscribers SET is_active = 0 WHERE telegram_id = ?",
                (telegram_id,)
            )
            return True
        except Exception as e:
            logger.error(f"Failed to remove subscriber: {e}")
            return False

    def is_premium(self, telegram_id: int) -> bool:
        sub = self.get_subscriber(telegram_id)
        if not sub:
            return False
        if not sub["is_active"]:
            return False
        if sub["plan"] not in ("premium", "vip"):
            return False
        if sub["end_date"]:
            try:
                end = datetime.fromisoformat(sub["end_date"])
                if end < datetime.now():
                    self.db.execute(
                        "UPDATE subscribers SET is_active = 0 WHERE telegram_id = ?",
                        (telegram_id,)
                    )
                    return False
            except (ValueError, TypeError):
                pass
        return True

    def is_active(self, telegram_id: int) -> bool:
        sub = self.get_subscriber(telegram_id)
        return bool(sub and sub["is_active"])

    def get_subscriber(self, telegram_id: int) -> dict:
        row = self.db.fetchone(
            "SELECT * FROM subscribers WHERE telegram_id = ?",
            (telegram_id,)
        )
        return dict(row) if row else None

    def list_subscribers(self, plan: str = None) -> list:
        if plan:
            rows = self.db.fetchall(
                "SELECT * FROM subscribers WHERE plan = ? ORDER BY created_at DESC",
                (plan,)
            )
        else:
            rows = self.db.fetchall(
                "SELECT * FROM subscribers ORDER BY created_at DESC"
            )
        return [dict(r) for r in rows] if rows else []

    def extend_subscription(self, telegram_id: int, days: int = 30) -> bool:
        sub = self.get_subscriber(telegram_id)
        if not sub:
            return False

        try:
            current_end = datetime.fromisoformat(sub["end_date"]) if sub["end_date"] else datetime.now()
            if current_end < datetime.now():
                current_end = datetime.now()
            new_end = current_end + timedelta(days=days)

            self.db.execute(
                "UPDATE subscribers SET end_date = ?, is_active = 1 WHERE telegram_id = ?",
                (new_end.isoformat(), telegram_id)
            )
            return True
        except Exception as e:
            logger.error(f"Failed to extend subscription: {e}")
            return False

    def get_stats(self) -> dict:
        total = self.db.fetchone("SELECT COUNT(*) as c FROM subscribers")
        active = self.db.fetchone("SELECT COUNT(*) as c FROM subscribers WHERE is_active = 1")
        premium = self.db.fetchone("SELECT COUNT(*) as c FROM subscribers WHERE plan = 'premium' AND is_active = 1")
        vip = self.db.fetchone("SELECT COUNT(*) as c FROM subscribers WHERE plan = 'vip' AND is_active = 1")

        expiring = self.check_expiring()

        return {
            "total_subscribers": total["c"] if total else 0,
            "active": active["c"] if active else 0,
            "premium": premium["c"] if premium else 0,
            "vip": vip["c"] if vip else 0,
            "expiring_soon": expiring,
        }

    def check_expiring(self, days_ahead: int = 3) -> list:
        threshold = (datetime.now() + timedelta(days=days_ahead)).isoformat()
        rows = self.db.fetchall("""
            SELECT telegram_id, username, end_date FROM subscribers
            WHERE is_active = 1 AND end_date <= ? AND end_date > ?
        """, (threshold, datetime.now().isoformat()))
        return [dict(r) for r in rows] if rows else []

    def log_activity(self, telegram_id: int, command: str, details: str = ""):
        try:
            self.db.execute("""
                INSERT INTO bot_activity_log (telegram_id, command, details)
                VALUES (?, ?, ?)
            """, (telegram_id, command, details))
        except Exception:
            pass
