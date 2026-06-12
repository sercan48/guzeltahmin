"""PHASE-LIVE L7 — Public delivery metrics and subscriber tracking.

PublicDeliveryMetrics: impressions, delivered signals, teaser volume,
publication latency (all deterministic, driven by injected Clock).

SubscriberTracker: free / active subscriber counts + engagement counters.

Additive: no changes to M1-M11 / L1-L6.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from ..monetization.clock import Clock, SystemClock


# ---------------------------------------------------------------------------
# Delivery metrics
# ---------------------------------------------------------------------------

@dataclass
class PublicDeliverySnapshot:
    impressions: int
    delivered: int
    teasers: int
    abbreviated: int
    suppressed_non_public: int
    publication_latency_p50_ms: Optional[float]
    publication_latency_p95_ms: Optional[float]
    delivery_rate: float

    def to_dict(self) -> dict:
        return {
            "impressions": self.impressions,
            "delivered": self.delivered,
            "teasers": self.teasers,
            "abbreviated": self.abbreviated,
            "suppressed_non_public": self.suppressed_non_public,
            "publication_latency_p50_ms": self.publication_latency_p50_ms,
            "publication_latency_p95_ms": self.publication_latency_p95_ms,
            "delivery_rate": round(self.delivery_rate, 4),
        }


class PublicDeliveryMetrics:
    """Counts public-channel impressions, deliveries, and publication latency."""

    def __init__(self, max_latency_samples: int = 1000) -> None:
        self._impressions = 0
        self._delivered = 0
        self._teasers = 0
        self._abbreviated = 0
        self._suppressed_non_public = 0
        self._latencies: List[float] = []
        self._max = max_latency_samples

    # ------------------------------------------------------------------ #

    def record_impression(self) -> None:
        """A signal reached the public-publication decision point."""
        self._impressions += 1

    def record_delivery(self, format_type: str, latency_ms: float = 0.0) -> None:
        """A teaser/abbreviated signal was published to the public channel."""
        self._delivered += 1
        if format_type == "TEASER":
            self._teasers += 1
        elif format_type == "ABBREVIATED":
            self._abbreviated += 1
        self._latencies.append(float(latency_ms))
        if len(self._latencies) > self._max:
            del self._latencies[0]

    def record_suppressed_non_public(self) -> None:
        """A signal was withheld from the public channel (not publishable)."""
        self._suppressed_non_public += 1

    def snapshot(self) -> PublicDeliverySnapshot:
        return PublicDeliverySnapshot(
            impressions=self._impressions,
            delivered=self._delivered,
            teasers=self._teasers,
            abbreviated=self._abbreviated,
            suppressed_non_public=self._suppressed_non_public,
            publication_latency_p50_ms=self._pct(50),
            publication_latency_p95_ms=self._pct(95),
            delivery_rate=(
                self._delivered / self._impressions if self._impressions else 0.0
            ),
        )

    # ------------------------------------------------------------------ #

    def _pct(self, p: float) -> Optional[float]:
        if not self._latencies:
            return None
        s = sorted(self._latencies)
        n = len(s)
        idx = max(0, min(n - 1, int(math.ceil(p / 100.0 * n)) - 1))
        return round(s[idx], 1)


# ---------------------------------------------------------------------------
# Subscriber tracking
# ---------------------------------------------------------------------------

@dataclass
class SubscriberSnapshot:
    total_free: int
    active_free: int
    total_engagements: int
    unique_engaged: int
    engagement_rate: float

    def to_dict(self) -> dict:
        return {
            "total_free": self.total_free,
            "active_free": self.active_free,
            "total_engagements": self.total_engagements,
            "unique_engaged": self.unique_engaged,
            "engagement_rate": round(self.engagement_rate, 4),
        }


class SubscriberTracker:
    """Tracks FREE subscriber counts and engagement counters.

    'active' = engaged within `active_window_seconds`. Engagement events are
    timestamped via the injected Clock so activeness is deterministic.
    """

    def __init__(
        self,
        clock: Optional[Clock] = None,
        active_window_seconds: float = 30 * 86400.0,
    ) -> None:
        self._clock = clock or SystemClock()
        self._window = active_window_seconds
        self._free_subscribers: set = set()
        self._engagements: int = 0
        self._last_engaged_ts: Dict[str, float] = {}

    # ------------------------------------------------------------------ #

    def add_subscriber(self, user_id: str) -> None:
        self._free_subscribers.add(user_id)

    def remove_subscriber(self, user_id: str) -> None:
        self._free_subscribers.discard(user_id)
        self._last_engaged_ts.pop(user_id, None)

    def record_engagement(self, user_id: str) -> None:
        """Record a subscriber interaction (view/click)."""
        self._free_subscribers.add(user_id)
        self._engagements += 1
        self._last_engaged_ts[user_id] = self._clock.now_ts()

    def snapshot(self) -> SubscriberSnapshot:
        now = self._clock.now_ts()
        active = sum(
            1 for ts in self._last_engaged_ts.values()
            if now - ts <= self._window
        )
        total = len(self._free_subscribers)
        unique_engaged = len(self._last_engaged_ts)
        return SubscriberSnapshot(
            total_free=total,
            active_free=active,
            total_engagements=self._engagements,
            unique_engaged=unique_engaged,
            engagement_rate=(unique_engaged / total if total else 0.0),
        )
