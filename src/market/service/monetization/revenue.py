"""RevenueSimulator — read-only MRR analytics over UserStore and delivery log.

Reads only from existing SQLite ledgers. Never writes to truth/control/signal
layers. No external APIs, no ML.

All monetary figures are estimates for planning purposes; no financial
transactions occur here.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from .clock import Clock
from .store import UserStore


@dataclass
class FunnelSnapshot:
    snapshot_at: str
    total_free: int
    total_basic: int
    total_pro: int
    total_active: int
    engaged_free_30d: int         # free users who received ≥1 signal in last 30 days
    upsell_events_30d: int
    mrr_estimate: float
    churn_risk_count: int         # users with no delivery in last 14 days (BASIC/PRO)


@dataclass
class MrrProjection:
    mrr_estimate: float
    n_free: int
    n_basic: int
    n_pro: int
    conversion_pipeline: float    # expected future conversions from engaged free users
    annual_run_rate: float


class RevenueSimulator:
    """Read-only analytics: MRR projection, funnel snapshot, churn indicators."""

    PRICE_BASIC: float = 9.99
    PRICE_PRO:   float = 24.99
    CONVERSION_RATE_BASIC: float = 0.05
    CONVERSION_RATE_PRO:   float = 0.02
    MONTHLY_CHURN_BASIC:   float = 0.10
    MONTHLY_CHURN_PRO:     float = 0.07

    def __init__(
        self,
        user_store: UserStore,
        delivery_db_path: str,
        clock: Clock,
    ) -> None:
        self._store = user_store
        self._delivery_db = delivery_db_path
        self._clock = clock

    # ------------------------------------------------------------------ #

    def funnel_snapshot(self) -> FunnelSnapshot:
        users = self._store.list_active_users()
        n_free  = sum(1 for u in users if u.tier == "FREE")
        n_basic = sum(1 for u in users if u.tier == "BASIC")
        n_pro   = sum(1 for u in users if u.tier == "PRO")

        now_ts   = self._clock.now_ts()
        day_30   = now_ts - 30 * 86400
        day_14   = now_ts - 14 * 86400

        engaged_free  = 0
        upsells_30d   = 0
        churn_risk    = 0

        try:
            dconn = sqlite3.connect(self._delivery_db, check_same_thread=False)
            dconn.row_factory = sqlite3.Row

            # Engaged free users: received ≥1 delivery in last 30 days
            rows = dconn.execute(
                """SELECT DISTINCT user_id FROM delivery_log
                   WHERE delivered_at_ts >= ? AND user_tier='FREE'""",
                (day_30,),
            ).fetchall()
            engaged_free = len(rows)

            # BASIC/PRO users with no delivery in last 14 days → churn risk
            paid_ids = {u.user_id for u in users if u.tier in ("BASIC", "PRO")}
            recently_active_ids: set = set()
            rows2 = dconn.execute(
                """SELECT DISTINCT user_id FROM delivery_log
                   WHERE delivered_at_ts >= ? AND user_tier IN ('BASIC','PRO')""",
                (day_14,),
            ).fetchall()
            recently_active_ids = {r["user_id"] for r in rows2}
            churn_risk = len(paid_ids - recently_active_ids)

            dconn.close()
        except Exception:
            pass  # delivery_log may not exist yet

        # Upsell events in last 30 days
        events = self._store.upsell_events()
        upsells_30d = sum(1 for e in events if e.created_at_ts >= day_30)

        mrr = self._calc_mrr(n_basic, n_pro)
        return FunnelSnapshot(
            snapshot_at=self._clock.now_iso(),
            total_free=n_free,
            total_basic=n_basic,
            total_pro=n_pro,
            total_active=len(users),
            engaged_free_30d=engaged_free,
            upsell_events_30d=upsells_30d,
            mrr_estimate=round(mrr, 2),
            churn_risk_count=churn_risk,
        )

    def mrr_projection(self) -> MrrProjection:
        snap = self.funnel_snapshot()
        mrr = self._calc_mrr(snap.total_basic, snap.total_pro)
        pipeline = snap.engaged_free_30d * self.CONVERSION_RATE_BASIC
        mrr_rounded = round(mrr, 2)
        return MrrProjection(
            mrr_estimate=mrr_rounded,
            n_free=snap.total_free,
            n_basic=snap.total_basic,
            n_pro=snap.total_pro,
            conversion_pipeline=round(pipeline, 2),
            annual_run_rate=round(mrr_rounded * 12, 2),
        )

    def churn_indicators(self) -> List[dict]:
        snap = self.funnel_snapshot()
        indicators = []
        if snap.churn_risk_count > 0:
            indicators.append({
                "type": "no_delivery_14d",
                "count": snap.churn_risk_count,
                "severity": "HIGH" if snap.churn_risk_count > snap.total_basic // 2 else "MEDIUM",
            })
        if snap.total_basic > 0 and snap.upsell_events_30d / max(snap.total_free, 1) < 0.1:
            indicators.append({
                "type": "low_upsell_trigger_rate",
                "upsell_events_30d": snap.upsell_events_30d,
                "severity": "MEDIUM",
            })
        return indicators

    # ------------------------------------------------------------------ #

    def _calc_mrr(self, n_basic: int, n_pro: int) -> float:
        return (
            n_basic * self.PRICE_BASIC * (1 - self.MONTHLY_CHURN_BASIC)
            + n_pro  * self.PRICE_PRO   * (1 - self.MONTHLY_CHURN_PRO)
        )
