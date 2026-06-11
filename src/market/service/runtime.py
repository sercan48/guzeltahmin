"""PHASE-LIVE L3 — ServiceRuntime: continuous production daemon.

Service loop flow (one iteration):
  1. IngestionBridge.process_due()  -> truth store updated, JobResult[]
  2. signal_source()                -> PaperSignal[]  (injected; None = data-only)
  3. ControlGateway.evaluate(metrics)
  4. for each signal: gateway.gate() -> publish or log-suppress
  5. for each SUCCESS OUTCOME job: ingest_outcome (settlement trigger)
  6. health.snapshot()

Crash recovery: all state lives in append-only SQLite ledgers.  Restart
re-opens the same DB files; the scheduler replays unobserved events, the
truth store has all ingested snapshots, the control plane re-verifies its
hash chain.

Additive only. No changes to M1-M10.3. No ML/prediction/betting logic.
"""

from __future__ import annotations

import logging
import signal as _signal
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

from ..activation import IngestionBridge, ProviderError
from ..control import (
    ControlGateway, ControlMetrics, TelemetryAdapter, ControlPlane, ControlConfig,
)
from ..scheduler import SnapshotScheduler, SystemClock
from ..truth import TruthStore
from .config import RuntimeConfig, ProviderConfig, TelegramConfig
from .health import HealthMonitor, HealthSnapshot
from .publisher import TelegramPublisher, PublishResult


@dataclass
class IterationSummary:
    jobs_processed: int = 0
    signals_gated: int = 0
    published: int = 0
    suppressed: int = 0
    outcomes_triggered: int = 0
    degraded: bool = False
    errors: List[str] = field(default_factory=list)
    latency_ms: float = 0.0

    def to_dict(self) -> dict:
        return {
            "jobs_processed": self.jobs_processed,
            "signals_gated": self.signals_gated,
            "published": self.published,
            "suppressed": self.suppressed,
            "outcomes_triggered": self.outcomes_triggered,
            "degraded": self.degraded,
            "errors": self.errors,
            "latency_ms": round(self.latency_ms, 1),
        }


class ServiceRuntime:
    """Long-running MIW production service.

    All dependencies are injected for testability. Use build_runtime() for
    production construction from RuntimeConfig.
    """

    def __init__(
        self,
        config: RuntimeConfig,
        scheduler: SnapshotScheduler,
        bridge: IngestionBridge,
        gateway: ControlGateway,
        publisher: TelegramPublisher,
        health: HealthMonitor,
        signal_source: Optional[Callable[[], List]] = None,
    ) -> None:
        self._config = config
        self._scheduler = scheduler
        self._bridge = bridge
        self._gateway = gateway
        self._publisher = publisher
        self._health = health
        self._signal_source = signal_source
        self._stop = False
        self._degraded = False
        self._consecutive_failures = 0
        self._log = logging.getLogger("miw.service")

    # ------------------------------------------------------------------ #
    # Core loop
    # ------------------------------------------------------------------ #

    def run_once(self) -> IterationSummary:
        """Single deterministic iteration. Testable without blocking."""
        t0 = time.monotonic()
        s = IterationSummary()

        # --- 1. Ingest due events ----------------------------------------
        try:
            results = self._bridge.process_due()
            s.jobs_processed = len(results)
            self._consecutive_failures = 0
            if self._degraded:
                self._degraded = False
                self._log.info("provider recovered; exiting degraded mode")
        except ProviderError as exc:
            s.errors.append(f"ingest: {exc}")
            self._consecutive_failures += 1
            if self._consecutive_failures >= self._config.degraded_failure_threshold:
                if not self._degraded:
                    self._log.warning(
                        "entering degraded mode after %d consecutive provider failures",
                        self._consecutive_failures,
                    )
                self._degraded = True
            s.degraded = self._degraded
            s.latency_ms = (time.monotonic() - t0) * 1000
            return s

        # --- 2. Generate signals from injected source --------------------
        signals: List = []
        if self._signal_source is not None:
            try:
                signals = list(self._signal_source())
            except Exception as exc:
                s.errors.append(f"signal_source: {exc}")
                self._log.warning("signal_source raised: %s", exc)

        # --- 3. Build control metrics and evaluate -----------------------
        try:
            metrics = self._build_metrics(results)
            self._gateway.evaluate(metrics)
        except Exception as exc:
            s.errors.append(f"control_eval: {exc}")
            self._log.warning("control evaluation failed: %s", exc)

        # --- 4. Gate and publish each signal -----------------------------
        for sig in signals:
            try:
                sid = self._signal_id(sig)
                gate = self._gateway.gate(sig, signal_id=sid)
                s.signals_gated += 1
                if gate.publish:
                    pub: PublishResult = self._publisher.publish(sig, gate)
                    if pub.published:
                        s.published += 1
                        self._health.record_signal(published=True)
                else:
                    s.suppressed += 1
                    self._health.record_signal(published=False)
                    self._log.info(
                        "signal suppressed sid=%s reason=%s", sid, gate.reason_codes
                    )
            except Exception as exc:
                s.errors.append(f"gate/publish: {exc}")
                self._log.warning("gate/publish error: %s", exc)

        # --- 5. Trigger settlement for OUTCOME jobs ----------------------
        for result in results:
            if getattr(result, "status", "") == "SUCCESS":
                job_id = getattr(result, "job_id", "")
                if job_id.endswith(":OUTCOME"):
                    try:
                        self._bridge.ingest_outcome(result.match_id)
                        self._health.record_settlement()
                        s.outcomes_triggered += 1
                    except Exception as exc:
                        s.errors.append(f"ingest_outcome: {exc}")

        # --- 6. Wrap up --------------------------------------------------
        s.degraded = self._degraded
        s.latency_ms = (time.monotonic() - t0) * 1000
        self._health.tick_iteration()
        return s

    # ------------------------------------------------------------------ #
    # Blocking run loop
    # ------------------------------------------------------------------ #

    def run(self) -> None:
        """Block until shutdown (SIGTERM/SIGINT). Bounded sleep between polls."""
        _signal.signal(_signal.SIGTERM, self._handle_signal)
        _signal.signal(_signal.SIGINT, self._handle_signal)

        self._log.info(
            "MIW service started (dry_run=%s poll=%.0fs)",
            self._config.telegram.dry_run,
            self._config.scheduler.poll_interval_seconds,
        )

        while not self._stop:
            try:
                summary = self.run_once()
                if summary.errors:
                    self._log.warning("iteration errors: %s", summary.errors)
                self._log.debug("iteration: %s", summary.to_dict())
            except Exception as exc:
                self._log.error("unhandled error in run_once: %s", exc, exc_info=True)

            # bounded sleep — checks stop flag every second
            deadline = time.monotonic() + self._config.scheduler.poll_interval_seconds
            while not self._stop and time.monotonic() < deadline:
                time.sleep(min(1.0, max(0.0, deadline - time.monotonic())))

        self._log.info("MIW service stopping")
        self._close()

    def shutdown(self, signum: int = 0, frame=None) -> None:
        """Request graceful shutdown (may be called from signal handler or tests)."""
        self._stop = True

    # ------------------------------------------------------------------ #
    # Health export
    # ------------------------------------------------------------------ #

    def health_snapshot(self) -> HealthSnapshot:
        mon = self._gateway.monitor()
        return self._health.snapshot(
            control_state=mon.get("active_state", "UNKNOWN"),
            risk_index=float(mon.get("risk_index", 0.0)),
            provider_health=self._provider_health(),
            completeness_score=self._completeness(),
            degraded=self._degraded,
        )

    # ------------------------------------------------------------------ #
    # Internal helpers
    # ------------------------------------------------------------------ #

    def _handle_signal(self, signum: int, frame) -> None:
        self._log.info("received signal %d; shutting down", signum)
        self._stop = True

    def _close(self) -> None:
        for obj in (self._bridge, self._gateway):
            try:
                obj.close()
            except Exception:
                pass

    def _build_metrics(self, results) -> ControlMetrics:
        mon = self._bridge.monitor()
        total = mon.get("ingestion_success", 0) + mon.get("ingestion_failure", 0)
        coverage = mon.get("ingestion_success", 1) / max(total, 1)
        return TelemetryAdapter.build(
            data_coverage=min(coverage, 1.0),
            truth_lag_norm=0.1,
            stability=80.0 if not self._degraded else 40.0,
        )

    @staticmethod
    def _signal_id(sig) -> str:
        ts = getattr(sig, "timestamp", "")
        mid = getattr(sig, "match_id", "")
        mkt = getattr(sig, "market", "")
        sel = getattr(sig, "selection", "")
        return f"{ts}:{mid}:{mkt}:{sel}"

    def _provider_health(self) -> Dict[str, dict]:
        try:
            transport = getattr(self._bridge, "transport", None)
            if transport is not None:
                return {getattr(transport, "name", "transport"): transport.health_snapshot()}
        except Exception:
            pass
        return {}

    def _completeness(self) -> float:
        try:
            mon = self._bridge.monitor()
            total = mon.get("ingestion_success", 0) + mon.get("ingestion_failure", 0)
            return mon.get("ingestion_success", 0) / max(total, 1)
        except Exception:
            return 0.0


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def build_runtime(
    config: RuntimeConfig,
    *,
    fixture_map=None,
    secret_provider=None,
    http_client=None,
    signal_source: Optional[Callable[[], List]] = None,
) -> ServiceRuntime:
    """Instantiate all dependencies from config.

    Injects:
      - secret_provider: defaults to EnvSecretProvider()
      - http_client: defaults to UrllibHttpClient (live); tests inject FakeHttpClient
      - fixture_map: FixtureMap() (empty; register matches at runtime)
      - signal_source: None = data-only mode (no signals published)
    """
    from ..activation import (
        FixtureMap, IngestionBridge,
        NullHttpClient, UrllibHttpClient, EnvSecretProvider,
        make_pinnacle_provider, make_betfair_provider,
    )
    from ..activation.betfair_outcome import make_betfair_outcome_provider
    from ..truth import TruthStore

    config.validate()

    sp = secret_provider or EnvSecretProvider()
    hc = http_client or UrllibHttpClient(timeout=8.0)
    fm = fixture_map or FixtureMap()

    clock = SystemClock()
    scheduler = SnapshotScheduler(clock=clock, db_path=config.scheduler.db_path)

    providers = []
    for pc in config.providers:
        if not pc.enabled:
            continue
        kwargs = dict(
            fixture_map=fm,
            http_client=hc,
            secret_provider=sp,
            rate_capacity=pc.rate_capacity,
            rate_refill_per_sec=pc.rate_refill_per_sec,
        )
        if pc.name == "pinnacle":
            providers.append(make_pinnacle_provider(**kwargs))
        elif pc.name == "betfair":
            providers.append(make_betfair_provider(
                **kwargs,
                session_secret=pc.session_secret,
                app_key_secret=pc.app_key_secret,
            ))
        elif pc.name == "betfair_outcome":
            providers.append(make_betfair_outcome_provider(
                **kwargs,
                session_secret=pc.session_secret,
                app_key_secret=pc.app_key_secret,
            ))

    truth = TruthStore(db_path=config.truth_db_path)
    bridge = IngestionBridge(scheduler, truth, providers, db_path=config.bridge_db_path)

    plane = ControlPlane(db_path=config.control_db_path)
    gateway = ControlGateway(plane, db_path=config.control_db_path + ".gate")

    bot_token = ""
    try:
        bot_token = sp.get(config.telegram.bot_token_secret)
    except Exception:
        pass  # dry-run or token not yet set

    publisher = TelegramPublisher(
        bot_token=bot_token,
        vip_channel_id=config.telegram.vip_channel_id,
        standard_channel_id=config.telegram.standard_channel_id,
        vip_tier_threshold=config.telegram.vip_tier_threshold,
        dry_run=config.telegram.dry_run,
        timeout=config.telegram.timeout,
    )

    return ServiceRuntime(
        config, scheduler, bridge, gateway, publisher,
        HealthMonitor(), signal_source,
    )
