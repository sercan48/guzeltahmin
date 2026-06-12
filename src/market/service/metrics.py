"""PHASE-LIVE L6 — Operational metrics and alert rule engine.

OperationalMetrics tracks uptime, provider availability, snapshot completeness,
and settlement/signal throughput across the service lifetime — all driven by
an injected Clock (deterministic).

AlertEngine evaluates IterationSummary + monitor snapshots against
AlertThresholds and emits structured alerts (degraded, outage, completeness,
replay-integrity).

Additive: reads only existing public APIs. No changes to M1-M11 / L1-L5.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional

from .monetization.clock import Clock, SystemClock
from .production import AlertThresholds


# ---------------------------------------------------------------------------
# Operational metrics
# ---------------------------------------------------------------------------

@dataclass
class OperationalSnapshot:
    uptime_seconds: float
    iterations: int
    empty_iterations: int
    consecutive_empty: int
    total_jobs: int
    provider_availability: float
    snapshot_completeness: float
    settlements_total: int
    signals_total: int
    degraded_iterations: int
    started_at_ts: float

    def to_dict(self) -> dict:
        return {
            "uptime_seconds": round(self.uptime_seconds, 1),
            "iterations": self.iterations,
            "empty_iterations": self.empty_iterations,
            "consecutive_empty": self.consecutive_empty,
            "total_jobs": self.total_jobs,
            "provider_availability": round(self.provider_availability, 4),
            "snapshot_completeness": round(self.snapshot_completeness, 4),
            "settlements_total": self.settlements_total,
            "signals_total": self.signals_total,
            "degraded_iterations": self.degraded_iterations,
            "started_at_ts": self.started_at_ts,
        }


class OperationalMetrics:
    """Lifetime operational counters, driven by an injected Clock."""

    def __init__(self, clock: Optional[Clock] = None) -> None:
        self._clock = clock or SystemClock()
        self._started_at = self._clock.now_ts()
        self._iterations = 0
        self._empty_iterations = 0
        self._consecutive_empty = 0
        self._total_jobs = 0
        self._healthy_iterations = 0   # iterations with no errors
        self._degraded_iterations = 0
        self._settlements = 0
        self._signals = 0

    # ------------------------------------------------------------------ #

    def record_iteration(self, summary) -> None:
        """Update counters from one IterationSummary."""
        self._iterations += 1
        jobs = getattr(summary, "jobs_processed", 0)
        self._total_jobs += jobs

        if jobs == 0:
            self._empty_iterations += 1
            self._consecutive_empty += 1
        else:
            self._consecutive_empty = 0

        if getattr(summary, "errors", None):
            pass  # error iteration; not counted as healthy
        else:
            self._healthy_iterations += 1

        if getattr(summary, "degraded", False):
            self._degraded_iterations += 1

        self._settlements += getattr(summary, "outcomes_triggered", 0)
        self._signals += getattr(summary, "published", 0)

    def snapshot(self, snapshot_completeness: float = 0.0) -> OperationalSnapshot:
        availability = (
            self._healthy_iterations / self._iterations
            if self._iterations else 1.0
        )
        return OperationalSnapshot(
            uptime_seconds=self._clock.now_ts() - self._started_at,
            iterations=self._iterations,
            empty_iterations=self._empty_iterations,
            consecutive_empty=self._consecutive_empty,
            total_jobs=self._total_jobs,
            provider_availability=availability,
            snapshot_completeness=snapshot_completeness,
            settlements_total=self._settlements,
            signals_total=self._signals,
            degraded_iterations=self._degraded_iterations,
            started_at_ts=self._started_at,
        )


# ---------------------------------------------------------------------------
# Alerts
# ---------------------------------------------------------------------------

class AlertSeverity(str, Enum):
    INFO = "INFO"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"


@dataclass
class Alert:
    rule: str
    severity: str
    message: str
    created_at_ts: float

    def to_dict(self) -> dict:
        return {
            "rule": self.rule,
            "severity": self.severity,
            "message": self.message,
            "created_at_ts": self.created_at_ts,
        }


class AlertEngine:
    """Evaluates operational state against thresholds, emits structured alerts.

    Stateless w.r.t. history except for an append-only alert log. Deduplication
    is the caller's responsibility (or via the dedupe flag below).
    """

    def __init__(
        self,
        thresholds: AlertThresholds,
        clock: Optional[Clock] = None,
    ) -> None:
        self._t = thresholds
        self._clock = clock or SystemClock()
        self._alerts: List[Alert] = []

    # ------------------------------------------------------------------ #

    def evaluate(
        self,
        summary,
        op_snapshot: OperationalSnapshot,
        replay_all_valid: bool = True,
    ) -> List[Alert]:
        """Evaluate one cycle; append + return any new alerts."""
        new: List[Alert] = []
        now = self._clock.now_ts()

        # Degraded mode
        if self._t.degraded_mode and getattr(summary, "degraded", False):
            new.append(Alert(
                "degraded_mode", AlertSeverity.CRITICAL.value,
                "Service in degraded mode — provider failures exceeded threshold",
                now,
            ))

        # Provider outage / low availability
        if op_snapshot.provider_availability < self._t.min_provider_availability:
            new.append(Alert(
                "provider_availability", AlertSeverity.WARNING.value,
                f"Provider availability {op_snapshot.provider_availability:.2%} "
                f"below {self._t.min_provider_availability:.2%}",
                now,
            ))

        # Consecutive empty iterations (possible outage)
        if op_snapshot.consecutive_empty >= self._t.max_consecutive_empty_iterations:
            new.append(Alert(
                "provider_outage", AlertSeverity.WARNING.value,
                f"No jobs processed for {op_snapshot.consecutive_empty} "
                f"consecutive iterations",
                now,
            ))

        # Completeness
        if op_snapshot.snapshot_completeness < self._t.min_completeness:
            new.append(Alert(
                "completeness", AlertSeverity.WARNING.value,
                f"Snapshot completeness {op_snapshot.snapshot_completeness:.2%} "
                f"below {self._t.min_completeness:.2%}",
                now,
            ))

        # Replay integrity
        if self._t.replay_chain_must_be_valid and not replay_all_valid:
            new.append(Alert(
                "replay_integrity", AlertSeverity.CRITICAL.value,
                "Replay chain integrity check FAILED",
                now,
            ))

        self._alerts.extend(new)
        return new

    def active_alerts(self) -> List[Alert]:
        return list(self._alerts)

    def alert_count(self) -> int:
        return len(self._alerts)

    def critical_count(self) -> int:
        return sum(
            1 for a in self._alerts
            if a.severity == AlertSeverity.CRITICAL.value
        )

    def to_dict(self) -> dict:
        return {
            "total": len(self._alerts),
            "critical": self.critical_count(),
            "alerts": [a.to_dict() for a in self._alerts],
        }
