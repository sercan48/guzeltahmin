"""PHASE-LIVE L3 — Runtime health and metrics monitor."""

from __future__ import annotations

import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Dict, List, Optional


@dataclass
class HealthSnapshot:
    timestamp: str
    control_state: str
    risk_index: float
    provider_health: Dict[str, dict]
    completeness_score: float
    signals_published: int
    signals_suppressed: int
    settlement_lag_seconds: Optional[float]
    degraded: bool
    iteration_count: int

    def to_dict(self) -> dict:
        return asdict(self)


class HealthMonitor:
    """Accumulates runtime counters; produces periodic HealthSnapshot."""

    def __init__(self) -> None:
        self._published = 0
        self._suppressed = 0
        self._iterations = 0
        self._last_settlement: Optional[float] = None   # monotonic

    def record_signal(self, *, published: bool) -> None:
        if published:
            self._published += 1
        else:
            self._suppressed += 1

    def record_settlement(self) -> None:
        self._last_settlement = time.monotonic()

    def tick_iteration(self) -> None:
        self._iterations += 1

    def snapshot(
        self,
        *,
        control_state: str = "UNKNOWN",
        risk_index: float = 0.0,
        provider_health: Optional[Dict[str, dict]] = None,
        completeness_score: float = 0.0,
        degraded: bool = False,
    ) -> HealthSnapshot:
        now = time.monotonic()
        lag = (now - self._last_settlement) if self._last_settlement is not None else None
        return HealthSnapshot(
            timestamp=datetime.now(timezone.utc).isoformat(),
            control_state=control_state,
            risk_index=risk_index,
            provider_health=provider_health or {},
            completeness_score=completeness_score,
            signals_published=self._published,
            signals_suppressed=self._suppressed,
            settlement_lag_seconds=round(lag, 1) if lag is not None else None,
            degraded=degraded,
            iteration_count=self._iterations,
        )

    def reset_counters(self) -> None:
        """Optional: zero rolling counters (e.g. between reporting periods)."""
        self._published = 0
        self._suppressed = 0
