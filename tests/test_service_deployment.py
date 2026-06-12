"""PHASE-LIVE L6 — Offline deterministic tests for production deployment layer.

No network. ManualClock for all time. tempfile/:memory: for all I/O.
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from dataclasses import dataclass, field
from typing import List, Optional
from unittest.mock import MagicMock

from src.market.activation.transport import StaticSecretProvider
from src.market.service.deployment import build_production_harness
from src.market.service.deployment_report import ProductionDailyReport
from src.market.service.metrics import (
    Alert, AlertEngine, AlertSeverity, OperationalMetrics, OperationalSnapshot,
)
from src.market.service.monetization.clock import ManualClock
from src.market.service.preflight import (
    CheckStatus, PreflightReport, ProviderValidator,
)
from src.market.service.production import AlertThresholds, ProductionProfile


_BASE_TS = 1767614400.0


# ---------------------------------------------------------------------------
# Stubs
# ---------------------------------------------------------------------------

@dataclass
class _Summary:
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
            "published": self.published,
            "outcomes_triggered": self.outcomes_triggered,
            "degraded": self.degraded,
            "errors": self.errors,
        }


# ===========================================================================
# ProductionProfile
# ===========================================================================

class TestProductionProfile(unittest.TestCase):

    def test_dry_run_forced_true(self):
        p = ProductionProfile()
        cfg = p.to_runtime_config()
        self.assertTrue(cfg.telegram.dry_run)

    def test_all_providers_enabled_by_default(self):
        p = ProductionProfile()
        names = [pc.name for pc in p.provider_configs()]
        self.assertIn("pinnacle", names)
        self.assertIn("betfair", names)
        self.assertIn("betfair_outcome", names)

    def test_disable_pinnacle(self):
        p = ProductionProfile(enable_pinnacle=False)
        names = [pc.name for pc in p.provider_configs()]
        self.assertNotIn("pinnacle", names)
        self.assertIn("betfair", names)

    def test_required_secrets_full(self):
        p = ProductionProfile()
        secrets = p.required_secrets()
        self.assertIn("PINNACLE_API_KEY", secrets)
        self.assertIn("BETFAIR_APP_KEY", secrets)
        self.assertIn("BETFAIR_SESSION_TOKEN", secrets)

    def test_required_secrets_deduplicated(self):
        # betfair + betfair_outcome both need the same betfair secrets
        p = ProductionProfile(enable_pinnacle=False)
        secrets = p.required_secrets()
        self.assertEqual(len(secrets), len(set(secrets)))

    def test_required_secrets_pinnacle_only(self):
        p = ProductionProfile(enable_betfair=False, enable_betfair_outcome=False)
        self.assertEqual(p.required_secrets(), ["PINNACLE_API_KEY"])

    def test_validate_passes(self):
        ProductionProfile().validate()  # should not raise

    def test_validate_rejects_no_providers(self):
        p = ProductionProfile(
            enable_pinnacle=False, enable_betfair=False,
            enable_betfair_outcome=False,
        )
        with self.assertRaises(ValueError):
            p.validate()

    def test_validate_rejects_bad_poll(self):
        p = ProductionProfile(poll_interval_seconds=0)
        with self.assertRaises(ValueError):
            p.validate()

    def test_custom_poll_interval(self):
        p = ProductionProfile(poll_interval_seconds=60.0)
        self.assertEqual(p.to_runtime_config().scheduler.poll_interval_seconds, 60.0)

    def test_db_paths_propagate(self):
        p = ProductionProfile(
            scheduler_db="s.db", truth_db="t.db",
            control_db="c.db", bridge_db="b.db",
        )
        cfg = p.to_runtime_config()
        self.assertEqual(cfg.scheduler.db_path, "s.db")
        self.assertEqual(cfg.truth_db_path, "t.db")
        self.assertEqual(cfg.control_db_path, "c.db")
        self.assertEqual(cfg.bridge_db_path, "b.db")


# ===========================================================================
# ProviderValidator (pre-flight)
# ===========================================================================

class TestProviderValidator(unittest.TestCase):

    def _secrets(self, present: dict):
        return StaticSecretProvider(present)

    def test_all_credentials_present_passes(self):
        sp = self._secrets({"PINNACLE_API_KEY": "x", "BETFAIR_APP_KEY": "y",
                            "BETFAIR_SESSION_TOKEN": "z"})
        v = ProviderValidator(sp)
        report = v.run(["PINNACLE_API_KEY", "BETFAIR_APP_KEY", "BETFAIR_SESSION_TOKEN"])
        self.assertTrue(report.passed)
        self.assertEqual(report.n_pass, 3)
        self.assertEqual(report.n_fail, 0)

    def test_missing_credential_fails(self):
        sp = self._secrets({"PINNACLE_API_KEY": "x"})
        v = ProviderValidator(sp)
        report = v.run(["PINNACLE_API_KEY", "BETFAIR_APP_KEY"])
        self.assertFalse(report.passed)
        self.assertEqual(report.n_fail, 1)

    def test_credential_check_never_exposes_value(self):
        sp = self._secrets({"SECRET_KEY": "super_secret_value"})
        v = ProviderValidator(sp)
        report = v.run(["SECRET_KEY"])
        dumped = json.dumps(report.to_dict())
        self.assertNotIn("super_secret_value", dumped)

    def test_reachability_skipped_without_probe(self):
        sp = self._secrets({"K": "v"})
        v = ProviderValidator(sp)
        report = v.run(["K"], endpoints=["https://api.example.com"])
        self.assertEqual(report.n_skip, 1)
        self.assertTrue(report.passed)  # skip is not fail

    def test_reachability_probe_pass(self):
        sp = self._secrets({"K": "v"})
        probe = lambda ep: (True, "200 OK")
        v = ProviderValidator(sp, reachability_probe=probe)
        report = v.run(["K"], endpoints=["https://api.example.com"])
        self.assertTrue(report.passed)
        self.assertEqual(report.n_pass, 2)  # 1 credential + 1 reachability

    def test_reachability_probe_fail(self):
        sp = self._secrets({"K": "v"})
        probe = lambda ep: (False, "connection refused")
        v = ProviderValidator(sp, reachability_probe=probe)
        report = v.run(["K"], endpoints=["https://api.example.com"])
        self.assertFalse(report.passed)

    def test_reachability_probe_exception_is_fail(self):
        sp = self._secrets({"K": "v"})
        def probe(ep):
            raise ConnectionError("boom")
        v = ProviderValidator(sp, reachability_probe=probe)
        report = v.run(["K"], endpoints=["https://api.example.com"])
        self.assertFalse(report.passed)

    def test_report_to_dict_structure(self):
        sp = self._secrets({"K": "v"})
        v = ProviderValidator(sp)
        d = v.run(["K"]).to_dict()
        for key in ("passed", "n_pass", "n_fail", "n_skip", "results"):
            self.assertIn(key, d)


# ===========================================================================
# OperationalMetrics
# ===========================================================================

class TestOperationalMetrics(unittest.TestCase):

    def setUp(self):
        self.clock = ManualClock(ts=_BASE_TS)
        self.m = OperationalMetrics(clock=self.clock)

    def test_uptime_tracks_clock(self):
        self.clock.advance(120.0)
        snap = self.m.snapshot()
        self.assertAlmostEqual(snap.uptime_seconds, 120.0, places=1)

    def test_iterations_counted(self):
        self.m.record_iteration(_Summary(jobs_processed=3))
        self.m.record_iteration(_Summary(jobs_processed=2))
        snap = self.m.snapshot()
        self.assertEqual(snap.iterations, 2)
        self.assertEqual(snap.total_jobs, 5)

    def test_empty_iterations_tracked(self):
        self.m.record_iteration(_Summary(jobs_processed=0))
        self.m.record_iteration(_Summary(jobs_processed=0))
        snap = self.m.snapshot()
        self.assertEqual(snap.empty_iterations, 2)
        self.assertEqual(snap.consecutive_empty, 2)

    def test_consecutive_empty_resets_on_jobs(self):
        self.m.record_iteration(_Summary(jobs_processed=0))
        self.m.record_iteration(_Summary(jobs_processed=5))
        snap = self.m.snapshot()
        self.assertEqual(snap.consecutive_empty, 0)

    def test_availability_with_errors(self):
        self.m.record_iteration(_Summary(jobs_processed=1))                  # healthy
        self.m.record_iteration(_Summary(jobs_processed=1, errors=["x"]))    # error
        snap = self.m.snapshot()
        self.assertAlmostEqual(snap.provider_availability, 0.5, places=3)

    def test_availability_perfect_when_no_iterations(self):
        snap = self.m.snapshot()
        self.assertEqual(snap.provider_availability, 1.0)

    def test_settlement_and_signal_throughput(self):
        self.m.record_iteration(_Summary(outcomes_triggered=2, published=3))
        self.m.record_iteration(_Summary(outcomes_triggered=1, published=1))
        snap = self.m.snapshot()
        self.assertEqual(snap.settlements_total, 3)
        self.assertEqual(snap.signals_total, 4)

    def test_degraded_iterations_counted(self):
        self.m.record_iteration(_Summary(degraded=True))
        self.m.record_iteration(_Summary(degraded=False))
        snap = self.m.snapshot()
        self.assertEqual(snap.degraded_iterations, 1)

    def test_snapshot_to_dict(self):
        self.m.record_iteration(_Summary(jobs_processed=1))
        d = self.m.snapshot(snapshot_completeness=0.9).to_dict()
        self.assertIn("uptime_seconds", d)
        self.assertEqual(d["snapshot_completeness"], 0.9)


# ===========================================================================
# AlertEngine
# ===========================================================================

class TestAlertEngine(unittest.TestCase):

    def setUp(self):
        self.clock = ManualClock(ts=_BASE_TS)
        self.thresholds = AlertThresholds()
        self.engine = AlertEngine(self.thresholds, clock=self.clock)

    def _op_snap(self, **kwargs):
        defaults = dict(
            uptime_seconds=100.0, iterations=10, empty_iterations=0,
            consecutive_empty=0, total_jobs=50, provider_availability=1.0,
            snapshot_completeness=1.0, settlements_total=0, signals_total=0,
            degraded_iterations=0, started_at_ts=_BASE_TS,
        )
        defaults.update(kwargs)
        return OperationalSnapshot(**defaults)

    def test_no_alerts_when_healthy(self):
        alerts = self.engine.evaluate(_Summary(), self._op_snap())
        self.assertEqual(len(alerts), 0)

    def test_degraded_mode_triggers_critical(self):
        alerts = self.engine.evaluate(_Summary(degraded=True), self._op_snap())
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0].rule, "degraded_mode")
        self.assertEqual(alerts[0].severity, AlertSeverity.CRITICAL.value)

    def test_low_availability_triggers_alert(self):
        alerts = self.engine.evaluate(
            _Summary(), self._op_snap(provider_availability=0.5)
        )
        rules = [a.rule for a in alerts]
        self.assertIn("provider_availability", rules)

    def test_consecutive_empty_triggers_outage(self):
        alerts = self.engine.evaluate(
            _Summary(), self._op_snap(consecutive_empty=200)
        )
        rules = [a.rule for a in alerts]
        self.assertIn("provider_outage", rules)

    def test_low_completeness_triggers_alert(self):
        alerts = self.engine.evaluate(
            _Summary(), self._op_snap(snapshot_completeness=0.5)
        )
        rules = [a.rule for a in alerts]
        self.assertIn("completeness", rules)

    def test_replay_failure_triggers_critical(self):
        alerts = self.engine.evaluate(
            _Summary(), self._op_snap(), replay_all_valid=False
        )
        rules = [a.rule for a in alerts]
        self.assertIn("replay_integrity", rules)
        crit = [a for a in alerts if a.rule == "replay_integrity"]
        self.assertEqual(crit[0].severity, AlertSeverity.CRITICAL.value)

    def test_alerts_accumulate(self):
        self.engine.evaluate(_Summary(degraded=True), self._op_snap())
        self.engine.evaluate(_Summary(degraded=True), self._op_snap())
        self.assertEqual(self.engine.alert_count(), 2)

    def test_critical_count(self):
        self.engine.evaluate(_Summary(degraded=True), self._op_snap())
        self.assertEqual(self.engine.critical_count(), 1)

    def test_to_dict_structure(self):
        self.engine.evaluate(_Summary(degraded=True), self._op_snap())
        d = self.engine.to_dict()
        self.assertIn("total", d)
        self.assertIn("critical", d)
        self.assertIn("alerts", d)

    def test_multiple_simultaneous_alerts(self):
        alerts = self.engine.evaluate(
            _Summary(degraded=True),
            self._op_snap(provider_availability=0.1, snapshot_completeness=0.1),
            replay_all_valid=False,
        )
        # degraded + availability + completeness + replay = 4
        self.assertGreaterEqual(len(alerts), 4)


# ===========================================================================
# ProductionDailyReport
# ===========================================================================

class TestProductionDailyReport(unittest.TestCase):

    def _report(self):
        return ProductionDailyReport().compile(
            day="2026-06-12",
            operational={"uptime_seconds": 3600},
            readiness={"overall": 88.0, "verdict": "CONDITIONAL_GO"},
            health={},
            monitoring={},
            settlement={"total_triggered": 5},
            replay={"all_valid": True},
            alerts={"total": 0, "critical": 0, "alerts": []},
            preflight={"passed": True},
        )

    def test_compile_required_keys(self):
        r = self._report()
        for key in ("day", "generated_at", "mode", "preflight", "operational",
                    "readiness", "health", "monitoring", "settlement",
                    "replay", "alerts"):
            self.assertIn(key, r)

    def test_mode_is_dry_run(self):
        self.assertEqual(self._report()["mode"], "DRY_RUN")

    def test_export_creates_file(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "prod-2026-06-12.json")
            ProductionDailyReport().export_json(path, self._report())
            self.assertTrue(os.path.exists(path))
            with open(path) as fh:
                self.assertEqual(json.load(fh)["day"], "2026-06-12")

    def test_export_creates_parent_dirs(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "a", "b", "prod.json")
            ProductionDailyReport().export_json(path, self._report())
            self.assertTrue(os.path.exists(path))


# ===========================================================================
# ProductionHarness — integration (mocked runtime)
# ===========================================================================

class TestProductionHarness(unittest.TestCase):

    def _make_harness(self, summaries=None, tmpdir=None):
        from src.market.service.deployment import ProductionHarness
        from src.market.service.monitor import FeedMonitor
        from src.market.service.report import SettlementVerifier, ReplayVerifier

        clock = ManualClock(ts=_BASE_TS)
        profile = ProductionProfile(
            poll_interval_seconds=0.0,
            report_dir=tmpdir or tempfile.mkdtemp(),
        )

        # Mock runtime
        runtime = MagicMock()
        self._summaries = list(summaries or [_Summary(jobs_processed=2)])
        self._idx = 0

        def run_once():
            s = self._summaries[min(self._idx, len(self._summaries) - 1)]
            self._idx += 1
            return s

        runtime.run_once.side_effect = run_once
        runtime.run_once_count = 0
        health = MagicMock()
        health.to_dict.return_value = {"control_state": "PAPER"}
        runtime.health_snapshot.return_value = health

        gateway = MagicMock()
        gateway.verify_chain.return_value = True
        gateway.replay.return_value = {
            "n_gated": 0, "n_published": 0, "n_suppressed": 0,
            "reason_counts": {}, "chain_valid": True,
        }

        harness = ProductionHarness(
            profile=profile,
            runtime=runtime,
            monitor=FeedMonitor(),
            op_metrics=OperationalMetrics(clock=clock),
            alert_engine=AlertEngine(profile.alerts, clock=clock),
            settlement_verifier=SettlementVerifier(),
            replay_verifier=ReplayVerifier(gateway),
            clock=clock,
        )
        return harness, runtime, clock

    def test_run_iteration_calls_run_once(self):
        h, runtime, _ = self._make_harness()
        h.run_iteration()
        runtime.run_once.assert_called_once()

    def test_run_iteration_returns_operational(self):
        h, _, _ = self._make_harness()
        result = h.run_iteration()
        self.assertIn("operational", result)
        self.assertIn("iteration", result)
        self.assertIn("new_alerts", result)

    def test_run_iteration_records_settlements(self):
        h, _, _ = self._make_harness([_Summary(outcomes_triggered=3)])
        h.run_iteration()
        self.assertIn("settlement", h._daily.compile(
            day="x", operational={}, readiness={"overall": 0},
            health={}, monitoring={}, settlement=h._settlement.report(),
            replay={}, alerts={"total": 0},
        ))
        self.assertEqual(h._settlement.report()["total_triggered"], 3)

    def test_degraded_summary_produces_alert(self):
        h, _, _ = self._make_harness([_Summary(jobs_processed=1, degraded=True)])
        result = h.run_iteration()
        rules = [a["rule"] for a in result["new_alerts"]]
        self.assertIn("degraded_mode", rules)

    def test_run_stops_at_max_iterations(self):
        h, runtime, _ = self._make_harness()
        h.run(max_iterations=3)
        self.assertEqual(runtime.run_once.call_count, 3)

    def test_export_daily_report_creates_file(self):
        with tempfile.TemporaryDirectory() as d:
            h, _, _ = self._make_harness(tmpdir=d)
            h.run_iteration()
            path = h.export_daily_report("2026-06-12")
            self.assertTrue(os.path.exists(path))
            with open(path) as fh:
                data = json.load(fh)
            self.assertEqual(data["mode"], "DRY_RUN")

    def test_shutdown_stops_runtime(self):
        h, runtime, _ = self._make_harness()
        h.shutdown()
        self.assertTrue(h._stop)
        runtime.shutdown.assert_called_once()

    def test_readiness_score_returns_verdict(self):
        h, _, _ = self._make_harness()
        score = h.readiness_score()
        self.assertIn("verdict", score)

    def test_operational_snapshot_dict(self):
        h, _, _ = self._make_harness()
        h.run_iteration()
        snap = h.operational_snapshot()
        self.assertIn("uptime_seconds", snap)
        self.assertEqual(snap["iterations"], 1)


# ===========================================================================
# build_production_harness factory (offline, FakeHttpClient/Static secrets)
# ===========================================================================

class TestBuildProductionHarness(unittest.TestCase):

    def _secrets(self):
        return StaticSecretProvider({
            "PINNACLE_API_KEY": "p", "BETFAIR_APP_KEY": "a",
            "BETFAIR_SESSION_TOKEN": "s",
        })

    def _tmp_profile(self, d):
        return ProductionProfile(
            poll_interval_seconds=30.0,
            scheduler_db=os.path.join(d, "s.db"),
            truth_db=os.path.join(d, "t.db"),
            control_db=os.path.join(d, "c.db"),
            bridge_db=os.path.join(d, "b.db"),
            report_dir=os.path.join(d, "reports"),
        )

    def test_factory_builds_harness(self):
        from src.market.activation import NullHttpClient
        with tempfile.TemporaryDirectory() as d:
            h = build_production_harness(
                self._tmp_profile(d),
                secret_provider=self._secrets(),
                http_client=NullHttpClient(),
                clock=ManualClock(ts=_BASE_TS),
            )
            self.assertIsNotNone(h)

    def test_factory_runs_preflight_pass(self):
        from src.market.activation import NullHttpClient
        with tempfile.TemporaryDirectory() as d:
            h = build_production_harness(
                self._tmp_profile(d),
                secret_provider=self._secrets(),
                http_client=NullHttpClient(),
                clock=ManualClock(ts=_BASE_TS),
            )
            self.assertTrue(h._preflight.passed)

    def test_factory_preflight_detects_missing_secret(self):
        from src.market.activation import NullHttpClient
        with tempfile.TemporaryDirectory() as d:
            h = build_production_harness(
                self._tmp_profile(d),
                secret_provider=StaticSecretProvider({"PINNACLE_API_KEY": "p"}),
                http_client=NullHttpClient(),
                clock=ManualClock(ts=_BASE_TS),
            )
            self.assertFalse(h._preflight.passed)

    def test_factory_dry_run_enforced(self):
        from src.market.activation import NullHttpClient
        with tempfile.TemporaryDirectory() as d:
            h = build_production_harness(
                self._tmp_profile(d),
                secret_provider=self._secrets(),
                http_client=NullHttpClient(),
                clock=ManualClock(ts=_BASE_TS),
            )
            self.assertTrue(h._runtime._publisher.dry_run)

    def test_factory_skip_preflight(self):
        from src.market.activation import NullHttpClient
        with tempfile.TemporaryDirectory() as d:
            h = build_production_harness(
                self._tmp_profile(d),
                secret_provider=self._secrets(),
                http_client=NullHttpClient(),
                clock=ManualClock(ts=_BASE_TS),
                run_preflight=False,
            )
            self.assertIsNone(h._preflight)


# ===========================================================================
# Restart recovery / replay determinism
# ===========================================================================

class TestRestartRecovery(unittest.TestCase):

    def test_runtime_reopens_same_dbs(self):
        """Two harnesses pointing at the same DB paths build without error."""
        from src.market.activation import NullHttpClient
        with tempfile.TemporaryDirectory() as d:
            profile = ProductionProfile(
                poll_interval_seconds=0.001,
                scheduler_db=os.path.join(d, "s.db"),
                truth_db=os.path.join(d, "t.db"),
                control_db=os.path.join(d, "c.db"),
                bridge_db=os.path.join(d, "b.db"),
                report_dir=os.path.join(d, "r"),
            )
            sp = StaticSecretProvider({
                "PINNACLE_API_KEY": "p", "BETFAIR_APP_KEY": "a",
                "BETFAIR_SESSION_TOKEN": "s",
            })
            h1 = build_production_harness(
                profile, secret_provider=sp, http_client=NullHttpClient(),
                clock=ManualClock(ts=_BASE_TS),
            )
            h1.run(max_iterations=2)
            h1._runtime.shutdown()

            # Restart: reopen same DBs
            h2 = build_production_harness(
                profile, secret_provider=sp, http_client=NullHttpClient(),
                clock=ManualClock(ts=_BASE_TS),
            )
            self.assertIsNotNone(h2)
            h2._runtime.shutdown()

    def test_replay_chain_valid_after_restart(self):
        from src.market.activation import NullHttpClient
        with tempfile.TemporaryDirectory() as d:
            profile = ProductionProfile(
                poll_interval_seconds=0.001,
                scheduler_db=os.path.join(d, "s.db"),
                truth_db=os.path.join(d, "t.db"),
                control_db=os.path.join(d, "c.db"),
                bridge_db=os.path.join(d, "b.db"),
                report_dir=os.path.join(d, "r"),
            )
            sp = StaticSecretProvider({
                "PINNACLE_API_KEY": "p", "BETFAIR_APP_KEY": "a",
                "BETFAIR_SESSION_TOKEN": "s",
            })
            h = build_production_harness(
                profile, secret_provider=sp, http_client=NullHttpClient(),
                clock=ManualClock(ts=_BASE_TS),
            )
            rc = h._replay.check()
            self.assertTrue(rc["chain_valid"])
            h._runtime.shutdown()


# ===========================================================================
# Additivity
# ===========================================================================

class TestAdditivity(unittest.TestCase):

    def test_m11_acceptance_hash_unchanged(self):
        import tests.test_m11_acceptance as m
        self.assertEqual(
            m.run_hash(m.baseline_providers()),
            m.TestM11Acceptance.BASELINE_HASH,
        )

    def test_existing_service_modules_import(self):
        import tests.test_service_runtime      # noqa: F401
        import tests.test_service_validation   # noqa: F401
        import tests.test_service_monetization # noqa: F401


if __name__ == "__main__":
    unittest.main(verbosity=2)
