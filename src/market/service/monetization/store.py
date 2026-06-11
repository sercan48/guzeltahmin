"""UserStore — SQLite-backed user registry with quota enforcement.

Schema (single file, multiple tables):
  users           — registration + quota counters
  upsell_events   — append-only upsell trigger log

All writes use explicit transactions; reads are read-only cursors.
Deterministic: all timestamps sourced from injected Clock.
"""

from __future__ import annotations

import sqlite3
from typing import List, Optional, Tuple

from .clock import Clock
from .models import (
    DAILY_QUOTA, WEEKLY_QUOTA, UpsellEvent, UserRecord, UserTier,
)


_DDL = """
CREATE TABLE IF NOT EXISTS users (
    user_id          TEXT PRIMARY KEY,
    tier             TEXT NOT NULL,
    channel_id       TEXT NOT NULL,
    active           INTEGER NOT NULL DEFAULT 1,
    joined_at_ts     REAL NOT NULL,
    signals_today    INTEGER NOT NULL DEFAULT 0,
    signals_week     INTEGER NOT NULL DEFAULT 0,
    last_reset_day   TEXT NOT NULL DEFAULT '',
    last_reset_week  TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS upsell_events (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id      TEXT NOT NULL,
    reason       TEXT NOT NULL,
    signal_id    TEXT NOT NULL DEFAULT '',
    created_at_ts REAL NOT NULL
);
"""


class UserStore:
    """User registry and quota enforcement layer."""

    def __init__(self, db_path: str, clock: Clock) -> None:
        self._clock = clock
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        for stmt in _DDL.strip().split(";"):
            stmt = stmt.strip()
            if stmt:
                self._conn.execute(stmt)
        self._conn.commit()

    # ------------------------------------------------------------------ #
    # User management
    # ------------------------------------------------------------------ #

    def register_user(
        self,
        user_id: str,
        tier: str,
        channel_id: str,
    ) -> UserRecord:
        """Insert or update a user record. Sets active=True."""
        now = self._clock.now_ts()
        today = self._clock.today_str()
        week = self._clock.week_str()
        with self._conn:
            self._conn.execute(
                """INSERT INTO users
                   (user_id, tier, channel_id, active, joined_at_ts,
                    signals_today, signals_week,
                    last_reset_day, last_reset_week)
                   VALUES (?, ?, ?, 1, ?, 0, 0, ?, ?)
                   ON CONFLICT(user_id) DO UPDATE SET
                     tier=excluded.tier,
                     channel_id=excluded.channel_id,
                     active=1""",
                (user_id, tier, channel_id, now, today, week),
            )
        return self.get_user(user_id)

    def deactivate_user(self, user_id: str) -> None:
        with self._conn:
            self._conn.execute(
                "UPDATE users SET active=0 WHERE user_id=?", (user_id,)
            )

    def get_user(self, user_id: str) -> Optional[UserRecord]:
        row = self._conn.execute(
            "SELECT * FROM users WHERE user_id=?", (user_id,)
        ).fetchone()
        return _row_to_user(row) if row else None

    def list_active_users(self) -> List[UserRecord]:
        rows = self._conn.execute(
            "SELECT * FROM users WHERE active=1"
        ).fetchall()
        return [_row_to_user(r) for r in rows]

    # ------------------------------------------------------------------ #
    # Quota enforcement
    # ------------------------------------------------------------------ #

    def check_and_consume_quota(
        self, user_id: str, signal_id: str = ""
    ) -> Tuple[bool, str]:
        """Check quota, reset if period rolled, consume if allowed.

        Returns (allowed: bool, reason: str).
        reason is '' when allowed, or 'daily_quota'/'weekly_quota' when denied.
        """
        today = self._clock.today_str()
        week = self._clock.week_str()

        with self._conn:
            row = self._conn.execute(
                "SELECT * FROM users WHERE user_id=? AND active=1", (user_id,)
            ).fetchone()
            if row is None:
                return False, "user_not_found"

            sig_today = row["signals_today"]
            sig_week  = row["signals_week"]

            # Reset daily counter if day rolled
            if row["last_reset_day"] != today:
                sig_today = 0
                self._conn.execute(
                    "UPDATE users SET signals_today=0, last_reset_day=? WHERE user_id=?",
                    (today, user_id),
                )

            # Reset weekly counter if week rolled
            if row["last_reset_week"] != week:
                sig_week = 0
                self._conn.execute(
                    "UPDATE users SET signals_week=0, last_reset_week=? WHERE user_id=?",
                    (week, user_id),
                )

            tier = UserTier(row["tier"])
            daily_limit  = DAILY_QUOTA[tier]
            weekly_limit = WEEKLY_QUOTA[tier]

            if sig_today >= daily_limit:
                return False, "daily_quota"
            if sig_week >= weekly_limit:
                return False, "weekly_quota"

            # Consume quota
            self._conn.execute(
                """UPDATE users
                   SET signals_today=signals_today+1,
                       signals_week=signals_week+1
                   WHERE user_id=?""",
                (user_id,),
            )
        return True, ""

    def record_upsell_event(
        self, user_id: str, reason: str, signal_id: str = ""
    ) -> None:
        """Append an upsell trigger event (never deleted)."""
        with self._conn:
            self._conn.execute(
                """INSERT INTO upsell_events (user_id, reason, signal_id, created_at_ts)
                   VALUES (?, ?, ?, ?)""",
                (user_id, reason, signal_id, self._clock.now_ts()),
            )

    def upsell_events(self, user_id: Optional[str] = None) -> List[UpsellEvent]:
        if user_id:
            rows = self._conn.execute(
                "SELECT * FROM upsell_events WHERE user_id=? ORDER BY id",
                (user_id,),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM upsell_events ORDER BY id"
            ).fetchall()
        return [
            UpsellEvent(
                event_id=r["id"],
                user_id=r["user_id"],
                reason=r["reason"],
                signal_id=r["signal_id"],
                created_at_ts=r["created_at_ts"],
            )
            for r in rows
        ]

    # ------------------------------------------------------------------ #

    def close(self) -> None:
        self._conn.close()


# ---------------------------------------------------------------------------
# Internal helper
# ---------------------------------------------------------------------------

def _row_to_user(row: sqlite3.Row) -> UserRecord:
    return UserRecord(
        user_id=row["user_id"],
        tier=row["tier"],
        channel_id=row["channel_id"],
        active=bool(row["active"]),
        joined_at_ts=row["joined_at_ts"],
        signals_today=row["signals_today"],
        signals_week=row["signals_week"],
        last_reset_day=row["last_reset_day"],
        last_reset_week=row["last_reset_week"],
    )
