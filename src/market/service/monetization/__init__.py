"""PHASE-LIVE L5 — Monetization Layer.

Subscription-based tiered signal delivery on top of M1-M11 pipeline.
Additive: zero changes to truth/control/scheduler/settlement/signal layers.

Quick start:
    from src.market.service.monetization import build_monetization
    filt, revenue = build_monetization(user_db_path="users.db", ...)
    # inject filt into ServiceRuntime(monetization_filter=filt)
"""

from .clock import Clock, ManualClock, SystemClock
from .delay import DelayScheduler
from .factory import build_monetization
from .filter import MonetizationFilter
from .models import (
    DAILY_QUOTA,
    FORMAT_RULES,
    TIER_DELAYS,
    WEEKLY_QUOTA,
    DeliveryRecord,
    FilterResult,
    FormatType,
    QueueEntry,
    UpsellEvent,
    UserRecord,
    UserTier,
)
from .revenue import FunnelSnapshot, MrrProjection, RevenueSimulator
from .store import UserStore
from .watermark import WatermarkInjector
from .worker import TieredDeliveryWorker

__all__ = [
    # clock
    "Clock", "SystemClock", "ManualClock",
    # models
    "UserTier", "FormatType", "UserRecord", "QueueEntry",
    "FilterResult", "DeliveryRecord", "UpsellEvent",
    "TIER_DELAYS", "FORMAT_RULES", "DAILY_QUOTA", "WEEKLY_QUOTA",
    # store
    "UserStore",
    # watermark
    "WatermarkInjector",
    # delay
    "DelayScheduler",
    # worker
    "TieredDeliveryWorker",
    # filter
    "MonetizationFilter",
    # revenue
    "RevenueSimulator", "FunnelSnapshot", "MrrProjection",
    # factory
    "build_monetization",
]
