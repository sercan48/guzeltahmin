"""build_monetization() — factory for the complete monetization stack.

All DB paths default to the same directory as the service DBs.
Inject ManualClock for testing; SystemClock for production.
"""

from __future__ import annotations

from typing import Optional

from .clock import Clock, SystemClock
from .delay import DelayScheduler
from .filter import MonetizationFilter
from .revenue import RevenueSimulator
from .store import UserStore
from .watermark import WatermarkInjector
from .worker import TieredDeliveryWorker


def build_monetization(
    *,
    user_db_path: str = "monetization_users.db",
    delay_queue_path: str = "monetization_delay.db",
    delivery_log_path: str = "monetization_delivery.db",
    clock: Optional[Clock] = None,
) -> tuple:
    """Instantiate and wire the full monetization stack.

    Returns (MonetizationFilter, RevenueSimulator).
    Inject the MonetizationFilter into ServiceRuntime as monetization_filter=...
    """
    if clock is None:
        clock = SystemClock()

    wm      = WatermarkInjector()
    store   = UserStore(user_db_path, clock)
    sched   = DelayScheduler(delay_queue_path, clock)
    worker  = TieredDeliveryWorker(sched, wm, delivery_log_path, clock)
    filt    = MonetizationFilter(store, sched, worker, wm, clock)
    revenue = RevenueSimulator(store, delivery_log_path, clock)

    return filt, revenue
