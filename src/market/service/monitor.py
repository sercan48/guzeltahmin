"""PHASE-LIVE L4 — Feed completeness, provider latency, truth-confidence monitoring.

Additive: reads only from existing public APIs. No changes to M1-M11.
"""

from __future__ import annotations

import math
import statistics
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional

from ..activation.providers import OddsProvider, ProviderError, ProviderOutcome, ProviderQuote


# ---------------------------------------------------------------------------
# Latency tracking
# ---------------------------------------------------------------------------

@dataclass
class LatencySample:
    provider: str
    latency_ms: float
    success: bool
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


class LatencyTracker:
    """Per-provider latency sample ring-buffer with percentile stats."""

    def __init__(self, max_samples: int = 1000) -> None:
        self._max = max_samples
        self._samples: Dict[str, List[LatencySample]] = {}

    def record(self, provider: str, latency_ms: float, success: bool) -> None:
        bucket = self._samples.setdefault(provider, [])
        bucket.append(LatencySample(provider, latency_ms, success))
        if len(bucket) > self._max:
            del bucket[0]

    def stats(self, provider: str) -> dict:
        bucket = self._samples.get(provider, [])
        if not bucket:
            return {"provider": provider, "count": 0}
        latencies = [s.latency_ms for s in bucket]
        failures = sum(1 for s in bucket if not s.success)
        n = len(latencies)
        sorted_l = sorted(latencies)

        def pct(p: float) -> float:
            idx = max(0, min(n - 1, int(math.ceil(p / 100.0 * n)) - 1))
            return round(sorted_l[idx], 1)

        return {
            "provider": provider,
            "count": n,
            "p50_ms": pct(50),
            "p95_ms": pct(95),
            "p99_ms": pct(99),
            "mean_ms": round(statistics.mean(latencies), 1),
            "failure_count": failures,
            "failure_rate": round(failures / n, 4),
        }

    def all_stats(self) -> Dict[str, dict]:
        return {p: self.stats(p) for p in self._samples}


# ---------------------------------------------------------------------------
# Feed completeness tracking
# ---------------------------------------------------------------------------

class CompletenessTracker:
    """Tracks feed ingestion completeness across service iterations."""

    def __init__(self) -> None:
        self._total_iterations = 0
        self._total_jobs = 0
        self._error_iterations = 0
        self._degraded_iterations = 0

    def record_iteration(
        self, jobs_processed: int, has_error: bool, degraded: bool
    ) -> None:
        self._total_iterations += 1
        self._total_jobs += jobs_processed
        if has_error:
            self._error_iterations += 1
        if degraded:
            self._degraded_iterations += 1

    def stats(self) -> dict:
        n = max(self._total_iterations, 1)
        return {
            "total_iterations": self._total_iterations,
            "total_jobs": self._total_jobs,
            "error_iterations": self._error_iterations,
            "degraded_iterations": self._degraded_iterations,
            "clean_ratio": round(
                (self._total_iterations - self._error_iterations) / n, 4
            ),
            "degraded_ratio": round(self._degraded_iterations / n, 4),
        }


# ---------------------------------------------------------------------------
# Truth-confidence distribution tracking
# ---------------------------------------------------------------------------

class TruthConfidenceTracker:
    """Accumulates truth_confidence samples from gated signals."""

    def __init__(self, below_threshold: float = 0.6) -> None:
        self._samples: List[float] = []
        self._threshold = below_threshold

    def record(self, confidence: float) -> None:
        self._samples.append(float(confidence))

    def stats(self) -> dict:
        if not self._samples:
            return {"count": 0}
        n = len(self._samples)
        s = sorted(self._samples)
        below = sum(1 for v in self._samples if v < self._threshold)

        def pct(p: float) -> float:
            idx = max(0, min(n - 1, int(math.ceil(p / 100.0 * n)) - 1))
            return round(s[idx], 4)

        return {
            "count": n,
            "mean": round(statistics.mean(self._samples), 4),
            "p10": pct(10),
            "p50": pct(50),
            "p90": pct(90),
            "below_threshold_count": below,
            "below_threshold_rate": round(below / n, 4),
            "threshold": self._threshold,
        }


# ---------------------------------------------------------------------------
# Aggregate monitor
# ---------------------------------------------------------------------------

class FeedMonitor:
    """Aggregates all L4 monitoring streams into one queryable object."""

    def __init__(
        self,
        latency_max_samples: int = 1000,
        truth_conf_threshold: float = 0.6,
    ) -> None:
        self.latency = LatencyTracker(max_samples=latency_max_samples)
        self.completeness = CompletenessTracker()
        self.truth_confidence = TruthConfidenceTracker(
            below_threshold=truth_conf_threshold
        )

    def record_iteration(self, summary, signals: Optional[List] = None) -> None:
        """Update all monitors from one IterationSummary + optional signal list."""
        self.completeness.record_iteration(
            jobs_processed=summary.jobs_processed,
            has_error=bool(getattr(summary, "errors", [])),
            degraded=bool(getattr(summary, "degraded", False)),
        )
        if signals:
            for sig in signals:
                tc = getattr(sig, "truth_confidence", None)
                if tc is not None:
                    self.truth_confidence.record(float(tc))

    def snapshot(self) -> dict:
        return {
            "latency": self.latency.all_stats(),
            "completeness": self.completeness.stats(),
            "truth_confidence": self.truth_confidence.stats(),
        }


# ---------------------------------------------------------------------------
# Provider latency instrumentation proxy
# ---------------------------------------------------------------------------

class MonitoringProvider(OddsProvider):
    """Wraps an OddsProvider to capture per-request latency without changing behavior."""

    def __init__(self, inner: OddsProvider, tracker: LatencyTracker) -> None:
        self._inner = inner
        self._tracker = tracker
        self.name: str = inner.name
        self.provider_class: str = inner.provider_class

    def fetch_snapshot(
        self, match_id: str, market: str, tick: str
    ) -> List[ProviderQuote]:
        t0 = time.monotonic()
        try:
            result = self._inner.fetch_snapshot(match_id, market, tick)
            self._tracker.record(self.name, (time.monotonic() - t0) * 1000, True)
            return result
        except ProviderError:
            self._tracker.record(self.name, (time.monotonic() - t0) * 1000, False)
            raise

    def fetch_outcome(self, match_id: str) -> Optional[ProviderOutcome]:
        t0 = time.monotonic()
        try:
            result = self._inner.fetch_outcome(match_id)
            self._tracker.record(self.name, (time.monotonic() - t0) * 1000, True)
            return result
        except ProviderError:
            self._tracker.record(self.name, (time.monotonic() - t0) * 1000, False)
            raise
