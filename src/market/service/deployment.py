"""PHASE-LIVE L6 — Production deployment harness for real-feed dry-run.

Composes:
  - ProductionProfile        → RuntimeConfig (dry_run forced True)
  - ProviderValidator        → startup pre-flight (credentials + reachability)
  - ServiceRuntime           → the L3 continuous loop (built via build_runtime)
  - FeedMonitor / L4 reports → completeness, latency, settlement, replay
  - OperationalMetrics       → uptime, availability, throughput
  - AlertEngine              → degraded / outage / completeness / replay alerts
  - DailySummary             → JSON operational report

Runs continuously in dry-run mode. No Telegram publishing. Restart-safe:
all pipeline state lives in append-only SQLite ledgers; on restart the harness
re-opens the same DBs and resumes.

Additive: zero changes to M1-M11 / L1-L5 behaviour.
"""

from __future__ import annotations

import datetime
import logging
import os
import signal as _signal
import time
from typing import Callable, List, Optional

from .deployment_report import ProductionDailyReport
from .metrics import AlertEngine, OperationalMetrics
from .monetization.clock import Clock, SystemClock
from .monitor import FeedMonitor, MonitoringProvider
from .preflight import PreflightReport, ProviderValidator
from .production import ProductionProfile
from .report import ReadinessScore, ReplayVerifier, SettlementVerifier
from .runtime import ServiceRuntime, build_runtime


class ProductionHarness:
    """Continuous real-feed dry-run harness with metrics, alerts, and reports."""

    def __init__(
        self,
        profile: ProductionProfile,
        runtime: ServiceRuntime,
        monitor: FeedMonitor,
        op_metrics: OperationalMetrics,
        alert_engine: AlertEngine,
        settlement_verifier: SettlementVerifier,
        replay_verifier: ReplayVerifier,
        clock: Clock,
        preflight: Optional[PreflightReport] = None,
    ) -> None:
        self._profile = profile
        self._runtime = runtime
        self._monitor = monitor
        self._op = op_metrics
        self._alerts = alert_engine
        self._settlement = settlement_verifier
        self._replay = replay_verifier
        self._clock = clock
        self._preflight = preflight
        self._readiness = ReadinessScore()
        self._daily = ProductionDailyReport()
        self._stop = False
        self._current_day: Optional[str] = None
        self._log = logging.getLogger("miw.production")

    # ------------------------------------------------------------------ #
    # Single iteration
    # ------------------------------------------------------------------ #

    def run_iteration(self) -> dict:
        """One service iteration + metric/alert update. Returns combined dict."""
        summary = self._runtime.run_once()

        self._monitor.record_iteration(summary)
        self._op.record_iteration(summary)
        if summary.outcomes_triggered > 0:
            self._settlement.record_triggered(summary.outcomes_triggered)

        completeness = self._monitor.completeness.stats().get("clean_ratio", 0.0)
        op_snap = self._op.snapshot(snapshot_completeness=completeness)
        replay_ok = self._replay.report().get("all_valid", True)
        new_alerts = self._alerts.evaluate(summary, op_snap, replay_all_valid=replay_ok)

        for a in new_alerts:
            self._log.warning("ALERT [%s] %s: %s", a.severity, a.rule, a.message)

        return {
            "iteration": summary.to_dict(),
            "operational": op_snap.to_dict(),
            "new_alerts": [a.to_dict() for a in new_alerts],
        }

    # ------------------------------------------------------------------ #
    # Blocking loop
    # ------------------------------------------------------------------ #

    def run(self, max_iterations: Optional[int] = None) -> None:
        """Block until shutdown (SIGTERM/SIGINT) or max_iterations.

        Writes a daily JSON report at each UTC-midnight rollover and a final
        report on shutdown. Runs a replay integrity check every 100 iterations.
        """
        _signal.signal(_signal.SIGTERM, self._handle_signal)
        _signal.signal(_signal.SIGINT, self._handle_signal)

        os.makedirs(self._profile.report_dir, exist_ok=True)
        self._log.info(
            "Production dry-run harness started (report_dir=%s max_iterations=%s)",
            self._profile.report_dir, max_iterations,
        )

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
                self.run_iteration()
            except Exception as exc:
                self._log.error("unhandled error in run_iteration: %s", exc, exc_info=True)

            n += 1
            if n % 100 == 0:
                rc = self._replay.check()
                if not rc.get("chain_valid", True):
                    self._log.error("REPLAY CHAIN FAILURE: %s", rc)

            poll = self._profile.poll_interval_seconds
            deadline = time.monotonic() + poll
            while not self._stop and time.monotonic() < deadline:
                time.sleep(min(1.0, max(0.0, deadline - time.monotonic())))

        if self._current_day:
            self._flush_daily(self._current_day)
        self._log.info("Production dry-run harness stopped after %d iterations", n)

    def shutdown(self) -> None:
        self._stop = True
        self._runtime.shutdown()

    # ------------------------------------------------------------------ #
    # Reports
    # ------------------------------------------------------------------ #

    def export_daily_report(self, day: str) -> str:
        return self._flush_daily(day)

    def readiness_score(self) -> dict:
        return self._readiness.breakdown(
            self._monitor.snapshot(),
            self._settlement.report(),
            self._replay.report(),
        )

    def operational_snapshot(self) -> dict:
        completeness = self._monitor.completeness.stats().get("clean_ratio", 0.0)
        return self._op.snapshot(snapshot_completeness=completeness).to_dict()

    # ------------------------------------------------------------------ #
    # Internal
    # ------------------------------------------------------------------ #

    def _handle_signal(self, signum: int, frame) -> None:
        self._log.info("received signal %d; stopping production harness", signum)
        self.shutdown()

    def _flush_daily(self, day: str) -> str:
        try:
            health = self._runtime.health_snapshot().to_dict()
        except Exception:
            health = {}
        report = self._daily.compile(
            day=day,
            operational=self.operational_snapshot(),
            readiness=self.readiness_score(),
            health=health,
            monitoring=self._monitor.snapshot(),
            settlement=self._settlement.report(),
            replay=self._replay.report(),
            alerts=self._alerts.to_dict(),
            preflight=self._preflight.to_dict() if self._preflight else None,
        )
        path = os.path.join(self._profile.report_dir, f"prod-{day}.json")
        self._daily.export_json(path, report)
        self._log.info(
            "daily production report written: %s (readiness=%.1f alerts=%d)",
            path, report["readiness"]["overall"], report["alerts"]["total"],
        )
        return path


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def build_production_harness(
    profile: ProductionProfile,
    *,
    fixture_map=None,
    secret_provider=None,
    http_client=None,
    signal_source: Optional[Callable[[], List]] = None,
    reachability_probe=None,
    clock: Optional[Clock] = None,
    run_preflight: bool = True,
    endpoints: Optional[List[str]] = None,
) -> ProductionHarness:
    """Build a fully-wired ProductionHarness from a ProductionProfile.

    Pre-flight credential/reachability checks run by default. Provider latency
    is captured by wrapping bridge.providers with MonitoringProvider.
    """
    from ..activation import EnvSecretProvider

    profile.validate()
    clock = clock or SystemClock()
    sp = secret_provider or EnvSecretProvider()

    # 1. Pre-flight validation (credentials + reachability)
    preflight_report: Optional[PreflightReport] = None
    if run_preflight:
        validator = ProviderValidator(sp, reachability_probe=reachability_probe)
        preflight_report = validator.run(profile.required_secrets(), endpoints or [])
        if not preflight_report.passed:
            logging.getLogger("miw.production").warning(
                "pre-flight checks FAILED: %s", preflight_report.to_dict()
            )

    # 2. Build the runtime (dry_run forced True by profile)
    runtime = build_runtime(
        profile.to_runtime_config(),
        fixture_map=fixture_map,
        secret_provider=sp,
        http_client=http_client,
        signal_source=signal_source,
    )

    # 3. Wrap providers for latency capture
    feed_monitor = FeedMonitor()
    try:
        providers = getattr(runtime._bridge, "providers", None)
        if providers is not None:
            runtime._bridge.providers = [
                p if isinstance(p, MonitoringProvider)
                else MonitoringProvider(p, feed_monitor.latency)
                for p in providers
            ]
    except Exception:
        pass

    return ProductionHarness(
        profile=profile,
        runtime=runtime,
        monitor=feed_monitor,
        op_metrics=OperationalMetrics(clock=clock),
        alert_engine=AlertEngine(profile.alerts, clock=clock),
        settlement_verifier=SettlementVerifier(),
        replay_verifier=ReplayVerifier(runtime._gateway),
        clock=clock,
        preflight=preflight_report,
    )
