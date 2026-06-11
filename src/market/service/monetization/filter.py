"""MonetizationFilter — routes gated signals to per-user delivery queues.

Input:  (signal, gate)  where gate.publish=True  (SUPPRESS/HALT never arrive)
Output: FilterResult — enqueue counts per user

Determinism:
  publish_after_ts = _gate_ts(signal, clock) + TIER_DELAYS[user_tier][signal_grade]
  Same (signal, clock, users) → identical queue entries → identical delivery.

Safety guard: if gate.publish=False this method returns an empty result without
enqueuing anything. This protects against accidental calls from outside the
runtime guard.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Optional

from .clock import Clock
from .delay import DelayScheduler
from .models import (
    FORMAT_RULES, TIER_DELAYS, FilterResult, FormatType, UserTier,
)
from .store import UserStore
from .watermark import WatermarkInjector
from .worker import TieredDeliveryWorker, _serialize_gate, _serialize_signal


_SIGNAL_FIELDS = (
    "match_id", "market", "selection", "edge_score", "tier",
    "confidence", "truth_confidence", "timestamp",
)

_KNOWN_GRADES = frozenset({"TIER_S", "TIER_A", "TIER_B", "TIER_C"})


def _gate_ts(signal, clock: Clock) -> float:
    """Parse signal.timestamp to Unix float; fall back to clock.now_ts()."""
    ts_str = getattr(signal, "timestamp", None)
    if ts_str:
        try:
            dt = datetime.fromisoformat(str(ts_str))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.timestamp()
        except (ValueError, TypeError, AttributeError):
            pass
    return clock.now_ts()


class MonetizationFilter:
    """Routes an ALLOW-gated signal to each active user's delay queue."""

    def __init__(
        self,
        user_store: UserStore,
        delay_scheduler: DelayScheduler,
        worker: TieredDeliveryWorker,
        watermark_injector: WatermarkInjector,
        clock: Clock,
    ) -> None:
        self._store     = user_store
        self._scheduler = delay_scheduler
        self._worker    = worker
        self._wm        = watermark_injector
        self._clock     = clock
        self._log       = logging.getLogger("miw.monetization.filter")

    # ------------------------------------------------------------------ #

    def process(self, signal, gate) -> FilterResult:
        """Enqueue signal for each active user according to tier policy.

        If gate.publish=False this is a no-op (safety guard).
        """
        signal_id = getattr(gate, "signal_id", "") or _derive_signal_id(signal)
        result = FilterResult(signal_id=signal_id)

        if not getattr(gate, "publish", False):
            self._log.warning(
                "process() called with gate.publish=False for sid=%s — skipped",
                signal_id,
            )
            return result

        grade = _normalise_grade(getattr(signal, "tier", "TIER_C"))
        gate_ts = _gate_ts(signal, self._clock)
        signal_json = _serialize_signal(signal)
        gate_json   = _serialize_gate(gate)

        users = self._store.list_active_users()
        result.active_users_seen = len(users)

        for user in users:
            try:
                user_tier = UserTier(user.tier)
            except ValueError:
                self._log.warning("unknown tier %r for user %s — skipped", user.tier, user.user_id)
                continue

            allowed, deny_reason = self._store.check_and_consume_quota(
                user.user_id, signal_id=signal_id
            )
            if not allowed:
                self._store.record_upsell_event(user.user_id, deny_reason, signal_id)
                result.upsells_sent += 1
                result.skipped_quota += 1
                continue

            delay_sec   = TIER_DELAYS[user_tier][grade]
            fmt         = FORMAT_RULES[(user_tier, grade)]
            publish_after = gate_ts + delay_sec

            qid = self._scheduler.enqueue(
                signal_id=signal_id,
                user_id=user.user_id,
                user_tier=user_tier.value,
                channel_id=user.channel_id,
                format_type=fmt.value,
                signal_json=signal_json,
                gate_json=gate_json,
                publish_after_ts=publish_after,
            )
            if qid is not None:
                result.enqueued += 1

        return result

    def deliver_due(self, publisher) -> int:
        """Delegate due delivery to TieredDeliveryWorker. Returns count."""
        return self._worker.deliver_due(publisher)

    def close(self) -> None:
        for obj in (self._store, self._scheduler, self._worker):
            try:
                obj.close()
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _normalise_grade(grade: str) -> str:
    return grade if grade in _KNOWN_GRADES else "TIER_C"


def _derive_signal_id(signal) -> str:
    ts  = getattr(signal, "timestamp", "")
    mid = getattr(signal, "match_id", "")
    mkt = getattr(signal, "market", "")
    sel = getattr(signal, "selection", "")
    return f"{ts}:{mid}:{mkt}:{sel}"
