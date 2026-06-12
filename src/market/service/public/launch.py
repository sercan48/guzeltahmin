"""PHASE-LIVE L7 — Public launch validation harness.

PublicPublisher takes ALLOW-gated signals and publishes teaser-only content to
the FREE channel, enforcing — at three independent layers — that no PRO/BASIC
full-format content ever reaches the public:

  1. PublicChannelProfile.is_publishable(grade)  — grade gating
  2. PublicChannelProfile.allowed_format_for_grade — FREE-tier FORMAT_RULES only
  3. assert_no_leakage(text)                       — content inspection guard

LaunchValidationHarness wraps a ProductionHarness-style loop with public
delivery metrics, subscriber tracking, and dashboard export.

Additive: no changes to M1-M11 / L1-L6.
"""

from __future__ import annotations

import datetime
import logging
import os
import signal as _signal
import time
from types import SimpleNamespace
from typing import Callable, List, Optional

from ..monetization.clock import Clock, SystemClock
from ..monetization.models import FormatType
from ..monetization.worker import _format_signal, _serialize_signal, _deserialize_signal
from .dashboard import DashboardExporter
from .metrics import PublicDeliveryMetrics, SubscriberTracker
from .profile import PublicChannelProfile


# Markers that prove full-format (PRO/BASIC) content — must never appear publicly.
_LEAKAGE_MARKERS = ("Edge Score", "Truth Confidence", "VIP SIGNAL")


class LeakageError(RuntimeError):
    """Raised when full-format content is about to reach the public channel."""


def assert_no_leakage(text: str) -> None:
    """Raise LeakageError if `text` contains PRO/BASIC full-format markers."""
    for marker in _LEAKAGE_MARKERS:
        if marker in text:
            raise LeakageError(f"gated content marker {marker!r} in public text")


class PublicPublisher:
    """Publishes teaser-only signals to the FREE channel (dry-run by default)."""

    def __init__(
        self,
        profile: PublicChannelProfile,
        delivery_metrics: PublicDeliveryMetrics,
        clock: Optional[Clock] = None,
        send_fn: Optional[Callable[[str, str], bool]] = None,
    ) -> None:
        self._profile = profile
        self._metrics = delivery_metrics
        self._clock = clock or SystemClock()
        self._send_fn = send_fn          # injected; None in pure dry-run
        self._log = logging.getLogger("miw.public")
        self._published_today = 0
        self._last_day: Optional[str] = None

    # ------------------------------------------------------------------ #

    def publish(self, signal, gate) -> dict:
        """Publish a gated signal to the public channel as teaser-only content.

        Returns a result dict. Never publishes if:
          - gate.publish is False (suppressed upstream)
          - grade is not publishable per profile
          - daily cap reached
        Always strips to FREE-tier format and asserts no leakage.
        """
        self._metrics.record_impression()
        result = {
            "published": False,
            "format": None,
            "reason": "",
            "channel": self._profile.channel_id,
        }

        # Hard guard: never publish suppressed signals.
        if not getattr(gate, "publish", False):
            result["reason"] = "gate_suppressed"
            self._metrics.record_suppressed_non_public()
            return result

        grade = getattr(signal, "tier", "TIER_C")
        if not self._profile.is_publishable(grade):
            result["reason"] = "not_publishable_grade"
            self._metrics.record_suppressed_non_public()
            return result

        # Daily cap (deterministic via clock day rollover)
        day = self._clock.today_str()
        if self._last_day != day:
            self._last_day = day
            self._published_today = 0
        if self._published_today >= self._profile.max_publications_per_day:
            result["reason"] = "daily_cap_reached"
            self._metrics.record_suppressed_non_public()
            return result

        # Resolve FREE-tier format and render via the shared L5 formatter.
        fmt = self._profile.allowed_format_for_grade(grade)
        sig_ns = _deserialize_signal(_serialize_signal(signal))
        text = _format_signal(sig_ns, fmt)

        # Triple-guard: content inspection before any send.
        assert_no_leakage(text)

        t0 = time.monotonic()
        if self._profile.dry_run or self._send_fn is None:
            sent = True            # dry-run: no real send
            result["reason"] = "dry_run"
        else:
            sent = bool(self._send_fn(self._profile.channel_id, text))
            result["reason"] = "" if sent else "send_failed"
        latency_ms = (time.monotonic() - t0) * 1000

        if sent:
            self._published_today += 1
            self._metrics.record_delivery(fmt, latency_ms=latency_ms)
            result["published"] = True
            result["format"] = fmt
        return result


# ---------------------------------------------------------------------------
# Launch validation harness
# ---------------------------------------------------------------------------

class LaunchValidationHarness:
    """Drives public dry-run launch: publish teasers, collect metrics, export."""

    def __init__(
        self,
        profile: PublicChannelProfile,
        publisher: PublicPublisher,
        delivery_metrics: PublicDeliveryMetrics,
        subscribers: SubscriberTracker,
        dashboard: DashboardExporter,
        clock: Clock,
        signal_source: Optional[Callable[[], List]] = None,
        gate_fn: Optional[Callable[[object], object]] = None,
        report_dir: str = "public_reports",
    ) -> None:
        self._profile = profile
        self._publisher = publisher
        self._metrics = delivery_metrics
        self._subscribers = subscribers
        self._dashboard = dashboard
        self._clock = clock
        self._signal_source = signal_source
        self._gate_fn = gate_fn
        self._report_dir = report_dir
        self._stop = False
        self._current_day: Optional[str] = None
        self._log = logging.getLogger("miw.public.launch")

    # ------------------------------------------------------------------ #

    def run_iteration(self, signals: Optional[List] = None) -> dict:
        """Publish a batch of gated signals to the public channel."""
        batch = signals if signals is not None else self._gather_signals()
        published = 0
        leaked = 0
        for sig in batch:
            gate = self._resolve_gate(sig)
            try:
                res = self._publisher.publish(sig, gate)
                if res["published"]:
                    published += 1
            except LeakageError as exc:
                # A leak attempt is a hard validation failure — count + log,
                # but never let it crash the loop or reach the channel.
                leaked += 1
                self._log.error("LEAKAGE BLOCKED: %s", exc)
        return {
            "batch_size": len(batch),
            "published": published,
            "leaked_blocked": leaked,
            "delivery": self._metrics.snapshot().to_dict(),
        }

    def verify_publication_rules(self, sample_signals: List) -> dict:
        """Offline check: every publishable signal yields teaser/abbreviated only."""
        violations: List[str] = []
        checked = 0
        for sig in sample_signals:
            grade = getattr(sig, "tier", "TIER_C")
            if not self._profile.is_publishable(grade):
                continue
            checked += 1
            fmt = self._profile.allowed_format_for_grade(grade)
            if fmt == FormatType.FULL.value:
                violations.append(f"{grade} → FULL")
            sig_ns = _deserialize_signal(_serialize_signal(sig))
            text = _format_signal(sig_ns, fmt)
            try:
                assert_no_leakage(text)
            except LeakageError as exc:
                violations.append(str(exc))
        return {
            "checked": checked,
            "violations": violations,
            "passed": len(violations) == 0,
        }

    def export_dashboard(
        self,
        day: str,
        readiness: dict,
        operational: dict,
        alerts: dict,
    ) -> str:
        self._dashboard.record_day(
            day=day,
            readiness_overall=readiness.get("overall", 0.0),
            provider_health=operational.get("provider_health", {}),
            signal_volume=self._metrics.snapshot().delivered,
        )
        snap = self._dashboard.compile_snapshot(
            day=day,
            public_delivery=self._metrics.snapshot().to_dict(),
            subscribers=self._subscribers.snapshot().to_dict(),
            readiness=readiness,
            operational=operational,
            alerts=alerts,
        )
        path = os.path.join(self._report_dir, f"public-{day}.json")
        self._dashboard.export_json(path, snap)
        return path

    def run(self, max_iterations: Optional[int] = None,
            poll_interval_seconds: float = 30.0) -> None:
        _signal.signal(_signal.SIGTERM, self._handle_signal)
        _signal.signal(_signal.SIGINT, self._handle_signal)
        os.makedirs(self._report_dir, exist_ok=True)
        n = 0
        while not self._stop:
            if max_iterations is not None and n >= max_iterations:
                break
            try:
                self.run_iteration()
            except Exception as exc:
                self._log.error("public iteration error: %s", exc, exc_info=True)
            n += 1
            deadline = time.monotonic() + poll_interval_seconds
            while not self._stop and time.monotonic() < deadline:
                time.sleep(min(1.0, max(0.0, deadline - time.monotonic())))

    def shutdown(self) -> None:
        self._stop = True

    # ------------------------------------------------------------------ #

    def _handle_signal(self, signum: int, frame) -> None:
        self._stop = True

    def _gather_signals(self) -> List:
        if self._signal_source is None:
            return []
        try:
            return list(self._signal_source())
        except Exception as exc:
            self._log.warning("signal_source raised: %s", exc)
            return []

    def _resolve_gate(self, sig) -> object:
        if self._gate_fn is not None:
            return self._gate_fn(sig)
        # Default: assume upstream already gated to ALLOW (publish=True).
        return SimpleNamespace(publish=True, signal_id=getattr(sig, "match_id", ""))


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def build_public_launch(
    profile: PublicChannelProfile,
    *,
    clock: Optional[Clock] = None,
    signal_source: Optional[Callable[[], List]] = None,
    gate_fn: Optional[Callable[[object], object]] = None,
    send_fn: Optional[Callable[[str, str], bool]] = None,
    report_dir: str = "public_reports",
) -> tuple:
    """Build the public launch stack. Returns (LaunchValidationHarness, metrics dict)."""
    profile.validate()
    clock = clock or SystemClock()

    delivery_metrics = PublicDeliveryMetrics()
    subscribers = SubscriberTracker(clock=clock)
    dashboard = DashboardExporter()
    publisher = PublicPublisher(profile, delivery_metrics, clock=clock, send_fn=send_fn)

    harness = LaunchValidationHarness(
        profile=profile,
        publisher=publisher,
        delivery_metrics=delivery_metrics,
        subscribers=subscribers,
        dashboard=dashboard,
        clock=clock,
        signal_source=signal_source,
        gate_fn=gate_fn,
        report_dir=report_dir,
    )
    return harness, {
        "delivery": delivery_metrics,
        "subscribers": subscribers,
        "dashboard": dashboard,
    }
