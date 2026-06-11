"""Domain model objects for the monetization layer.

All are immutable dataclasses — pure value objects read from / written to SQLite.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional


class UserTier(str, Enum):
    FREE = "FREE"
    BASIC = "BASIC"
    PRO = "PRO"


class FormatType(str, Enum):
    FULL = "FULL"
    ABBREVIATED = "ABBREVIATED"
    TEASER = "TEASER"


# ---------------------------------------------------------------------------
# Per-tier policy constants (single source of truth)
# ---------------------------------------------------------------------------

#: Delay in seconds: TIER_DELAYS[user_tier][signal_grade] → seconds to wait
TIER_DELAYS: dict = {
    UserTier.FREE:  {"TIER_S": 4 * 3600, "TIER_A": 4 * 3600,
                     "TIER_B": 4 * 3600, "TIER_C": 4 * 3600},
    UserTier.BASIC: {"TIER_S": 15 * 60,  "TIER_A": 15 * 60,
                     "TIER_B": 0,         "TIER_C": 0},
    UserTier.PRO:   {"TIER_S": 0,         "TIER_A": 0,
                     "TIER_B": 0,         "TIER_C": 0},
}

#: Format rules: FORMAT_RULES[(user_tier, signal_grade)] → FormatType
FORMAT_RULES: dict = {
    (UserTier.FREE,  "TIER_S"): FormatType.TEASER,
    (UserTier.FREE,  "TIER_A"): FormatType.TEASER,
    (UserTier.FREE,  "TIER_B"): FormatType.ABBREVIATED,
    (UserTier.FREE,  "TIER_C"): FormatType.ABBREVIATED,
    (UserTier.BASIC, "TIER_S"): FormatType.FULL,
    (UserTier.BASIC, "TIER_A"): FormatType.FULL,
    (UserTier.BASIC, "TIER_B"): FormatType.FULL,
    (UserTier.BASIC, "TIER_C"): FormatType.FULL,
    (UserTier.PRO,   "TIER_S"): FormatType.FULL,
    (UserTier.PRO,   "TIER_A"): FormatType.FULL,
    (UserTier.PRO,   "TIER_B"): FormatType.FULL,
    (UserTier.PRO,   "TIER_C"): FormatType.FULL,
}

# Daily / weekly quota per tier (PRO = effectively unlimited)
DAILY_QUOTA:  dict = {UserTier.FREE: 2,  UserTier.BASIC: 10, UserTier.PRO: 10_000}
WEEKLY_QUOTA: dict = {UserTier.FREE: 5,  UserTier.BASIC: 30, UserTier.PRO: 10_000}


# ---------------------------------------------------------------------------
# Value objects
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class UserRecord:
    user_id: str
    tier: str            # UserTier value
    channel_id: str
    active: bool
    joined_at_ts: float
    signals_today: int
    signals_week: int
    last_reset_day: str  # "YYYY-MM-DD"
    last_reset_week: str # "YYYY-WW"


@dataclass(frozen=True)
class QueueEntry:
    queue_id: int
    signal_id: str
    user_id: str
    user_tier: str       # UserTier value
    channel_id: str
    format_type: str     # FormatType value
    signal_json: str
    gate_json: str
    publish_after_ts: float
    created_at_ts: float
    delivered_at_ts: Optional[float]


@dataclass
class FilterResult:
    signal_id: str
    enqueued: int = 0
    upsells_sent: int = 0
    skipped_quota: int = 0
    active_users_seen: int = 0

    def to_dict(self) -> dict:
        return {
            "signal_id": self.signal_id,
            "enqueued": self.enqueued,
            "upsells_sent": self.upsells_sent,
            "skipped_quota": self.skipped_quota,
            "active_users_seen": self.active_users_seen,
        }


@dataclass(frozen=True)
class DeliveryRecord:
    delivery_id: int
    queue_id: int
    signal_id: str
    user_id: str
    user_tier: str
    channel_id: str
    format_type: str
    published: bool
    delivered_at_ts: float
    reason: str


@dataclass(frozen=True)
class UpsellEvent:
    event_id: int
    user_id: str
    reason: str          # "daily_quota" | "weekly_quota"
    signal_id: str
    created_at_ts: float
