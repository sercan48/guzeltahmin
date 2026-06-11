"""PHASE-LIVE L4 — Dry-run validation harness for 14-30 day real-feed observation.

Wraps ServiceRuntime with monitoring, verification, and daily reporting.
Additive: zero changes to M1-M11 behaviour.
"""

from __future__ import annotations

import datetime
import logging
import os
import signal as _signal
import time
from typing import Callable, List, Optional

from .config import ProviderConfig, RuntimeConfig, SchedulerConfig, TelegramConfig
from .monitor import FeedMonitor, MonitoringProvider
from .report import DailySummary, ReadinessScore, ReplayVerifier, SettlementVerifier
from .runtime import ServiceRuntime, build_runtime


# ---------------------------------------------------------------------------
# Deployment profile
# ---------------------------------------------------------------------------

def make_l4_dry_run_profile(
    *,
    scheduler_db: str = "scheduler.db",
    truth_db: str = "truth.db",
    control_db: str = "control.db",
    bridge_db: str = "bridge.db",
    poll_interval_seconds: float = 30.0,
    vip_tier_threshold: str = "TIER_A",
    degraded_failure_threshold: int = 5,
) -> RuntimeConfig:
    """RuntimeConfig pre-wired for L4 dry-run validation (14–30 day window).

    All three providers enabled; Telegram dry_run=True (no messages sent).
    """
    return RuntimeConfig(
        providers=[
            ProviderConfig(
                name="pinnacle",
                enabled=True,
                rate_capacity=1.0,
                rate_refill_per_sec=0.5,
            ),
            ProviderConfig(
                name="betfair",
                enabled=True,
                rate_capacity=1.0,
                rate_refill_per_sec=0.5,
            ),
            ProviderConfig(
                name="betfair_outcome",
                enabled=True,
                rate_capacity=1.0,
                rate_refill_per_sec=0.5,
            ),
        ],
        scheduler=SchedulerConfig(
            db_path=scheduler_db,
            poll_interval_seconds=poll_interval_seconds,
            grace_seconds=3600.0,
        ),
        telegram=TelegramConfig(
            dry_run=True,
            vip_tier_threshold=vip_tier_threshold,
        ),
        truth_db_path=truth_db,
        control_db_path=control_db,
        bridge_db_path=bridge_db,
        log_level="INFO",
        degraded_failure_threshold=degraded_failure_threshold,
    )


# ---------------------------------------------------------------------------
# Validation harness
# ---------------------------------------------------------------------------

class ValidationHarness:
    """Wraps ServiceRuntime with L4 monitoring, verification, and daily reporting.

    One iteration = run_once() + update all monitors + optionally write daily report.
    """

    def __init__(
        self,
        runtime: ServiceRuntime,
        monitor: FeedMonitor,
        settlement_verifier: SettlementVerifier,
        replay_verifier: ReplayVerifier,
        report_dir: str = "l4_reports",
    ) -> None:
        self._runtime = runtime
        self._monitor = monitor
        self._settlement = settlement_verifier
        self._replay = replay_verifier
        self._daily = DailySummary()
        self._readiness = ReadinessScore()
        self._report_dir = report_dir
        self._stop = False
        self._current_day: Optional[str] = None
        self._iteration_count = 0
        self._log = logging.getLogger("miw.validation")

    # ------------------------------------------------------------------ #
    # Single iteration
    # ------------------------------------------------------------------ #

    def run_iteration(self) -> dict:
        """One run_once() + monitor update. Returns combined dict."""
        summary = self._runtime.run_once()
        self._monitor.record_iteration(summary)
        if summary.outcomes_triggered > 0:
            self._settlement.record_triggered(summary.outcomes_triggered)
        self._iteration_count += 1
        score = self.readiness_score()
        return {
            "iteration": summary.to_dict(),
            "readiness": score,
        }

    # ------------------------------------------------------------------ #
    # Blocking loop
    # ------------------------------------------------------------------ #

    def run(self, max_iterations: Optional[int] = None) -> None:
        """Block until shutdown (SIGTERM/SIGINT) or max_iterations reached.

        Writes a daily JSON summary at each UTC midnight rollover.
        Runs a replay integrity check every 100 iterations.
        """
        _signal.signal(_signal.SIGTERM, self._handle_signal)
        _signal.signal(_signal.SIGINT, self._handle_signal)

        self._log.info(
            "L4 validation started (report_dir=%s max_iterations=%s)",
            self._report_dir,
            max_iterations,
        )
        os.makedirs(self._report_dir, exist_ok=True)

        n = 0
        while not self._stop:
            if max_iterations is not None and n >= max_iterations:
                break

            day = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
            if self._current_day is None:
                self._current_day = day
            elif day != self._current_day:
                self._flush_daily(self._current_day)
                self._current_day = day

            try:
                result = self.run_iteration()
                self._log.debug("validation iteration %d: %s", n, result["iteration"])
            except Exception as exc:
                self._log.error("unhandled error in run_iteration: %s", exc, exc_info=True)

            n += 1

            if n % 100 == 0:
                rc = self._replay.check()
                if not rc.get("chain_valid", True):
                    self._log.error("REPLAY CHAIN FAILURE detected: %s", rc)

            poll = self._runtime._config.scheduler.poll_interval_seconds
            deadline = time.monotonic() + poll
            while not self._stop and time.monotonic() < deadline:
                time.sleep(min(1.0, max(0.0, deadline - time.monotonic())))

        if self._current_day:
            self._flush_daily(self._current_day)
        self._log.info("L4 validation stopped after %d iterations", n)

    def shutdown(self) -> None:
        """Request graceful stop (sets flag; no forced kill)."""
        self._stop = True
        self._runtime.shutdown()

    # ------------------------------------------------------------------ #
    # Reports and score
    # ------------------------------------------------------------------ #

    def export_daily_summary(self, day: str) -> str:
        """Write daily report to <report_dir>/<day>.json. Returns path."""
        return self._flush_daily(day)

    def readiness_score(self) -> dict:
        """Compute and return current composite readiness score breakdown."""
        return self._readiness.breakdown(
            self._monitor.snapshot(),
            self._settlement.report(),
            self._replay.report(),
        )

    # ------------------------------------------------------------------ #
    # Internal helpers
    # ------------------------------------------------------------------ #

    def _handle_signal(self, signum: int, frame) -> None:
        self._log.info("received signal %d; stopping L4 validation", signum)
        self.shutdown()

    def _flush_daily(self, day: str) -> str:
        try:
            health_dict = self._runtime.health_snapshot().to_dict()
        except Exception:
            health_dict = {}
        summary = self._daily.compile(
            day=day,
            monitor_snap=self._monitor.snapshot(),
            settlement_report=self._settlement.report(),
            replay_report=self._replay.report(),
            health_snap_dict=health_dict,
            readiness_breakdown=self.readiness_score(),
        )
        path = os.path.join(self._report_dir, f"{day}.json")
        self._daily.export_json(path, summary)
        self._log.info(
            "daily summary written: %s (readiness=%.1f verdict=%s)",
            path,
            summary["readiness"]["overall"],
            summary["readiness"]["verdict"],
        )
        return path


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def build_validation_harness(
    config: RuntimeConfig,
    *,
    fixture_map=None,
    secret_provider=None,
    http_client=None,
    signal_source: Optional[Callable[[], List]] = None,
    report_dir: str = "l4_reports",
) -> ValidationHarness:
    """Build a ValidationHarness from RuntimeConfig.

    Wraps providers in MonitoringProvider for per-request latency capture.
    If signal_source is provided, wraps it to capture truth_confidence samples.
    """
    feed_monitor = FeedMonitor()

    # Wrap signal_source to capture truth_confidence if one is injected
    monitoring_signal_source = None
    if signal_source is not None:
        tc_tracker = feed_monitor.truth_confidence

        def monitoring_signal_source() -> List:  # type: ignore[misc]
            signals = list(signal_source())
            for sig in signals:
                tc = getattr(sig, "truth_confidence", None)
                if tc is not None:
                    tc_tracker.record(float(tc))
            return signals

    runtime = build_runtime(
        config,
        fixture_map=fixture_map,
        secret_provider=secret_provider,
        http_client=http_client,
        signal_source=monitoring_signal_source,
    )

    # Wrap bridge.providers in-place for per-request latency tracking.
    # bridge.providers is the public list attribute of IngestionBridge.
    try:
        bridge_providers = getattr(runtime._bridge, "providers", None)
        if bridge_providers is not None:
            wrapped = [
                MonitoringProvider(p, feed_monitor.latency)
                if not isinstance(p, MonitoringProvider)
                else p
                for p in bridge_providers
            ]
            runtime._bridge.providers = wrapped
    except Exception:
        # If wrapping fails for any reason, monitoring runs without latency data
        pass

    return ValidationHarness(
        runtime=runtime,
        monitor=feed_monitor,
        settlement_verifier=SettlementVerifier(),
        replay_verifier=ReplayVerifier(runtime._gateway),
        report_dir=report_dir,
    )
