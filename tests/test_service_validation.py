"""PHASE-LIVE L4 — Offline tests for validation harness, monitoring, and reports.

Fully deterministic. No network. No real filesystem writes (tempfile only).
"""

from __future__ import annotations

import json
import os
import tempfile
import time
import unittest
from dataclasses import dataclass, field
from typing import List, Optional
from unittest.mock import MagicMock, patch

from src.market.activation.providers import (
    MockOddsProvider,
    ProviderError,
    ProviderOutcome,
    ProviderQuote,
)
from src.market.service.monitor import (
    CompletenessTracker,
    FeedMonitor,
    LatencyTracker,
    MonitoringProvider,
    TruthConfidenceTracker,
)
from src.market.service.report import (
    DailySummary,
    ReadinessScore,
    ReplayVerifier,
    SettlementVerifier,
    _verdict,
)
from src.market.service.validation import make_l4_dry_run_profile


# ---------------------------------------------------------------------------
# Minimal stubs
# ---------------------------------------------------------------------------

@dataclass
class _FakeSummary:
    """Minimal IterationSummary mimic for testing."""
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
            "latency_ms": self.latency_ms,
        }


@dataclass
class _FakeSignal:
    match_id: str = "m1"
    market: str = "1X2"
    selection: str = "HOME"
    tier: str = "TIER_A"
    edge_score: float = 0.05
    confidence: float = 0.80
    truth_confidence: float = 0.75
    timestamp: str = "2026-06-11T12:00:00+00:00"


class _FakeHealthSnapshot:
    def to_dict(self) -> dict:
        return {
            "timestamp": "2026-06-11T12:00:00+00:00",
            "control_state": "PAPER",
            "risk_index": 0.10,
            "provider_health": {},
            "completeness_score": 0.90,
            "signals_published": 2,
            "signals_suppressed": 0,
            "settlement_lag_seconds": None,
            "degraded": False,
            "iteration_count": 10,
        }


class _FakeRuntime:
    """Minimal ServiceRuntime stub for harness tests."""

    def __init__(self, summary=None) -> None:
        self._summary = summary or _FakeSummary()
        self._stop = False

        # Build a minimal _config stub
        class _Sched:
            poll_interval_seconds = 0.0

        class _Cfg:
            scheduler = _Sched()

        self._config = _Cfg()
        self.run_once_calls = 0

    def run_once(self) -> _FakeSummary:
        self.run_once_calls += 1
        return self._summary

    def health_snapshot(self) -> _FakeHealthSnapshot:
        return _FakeHealthSnapshot()

    def shutdown(self) -> None:
        self._stop = True


class _FakeGateway:
    def __init__(self, chain_valid: bool = True, replay_data: Optional[dict] = None) -> None:
        self._chain_valid = chain_valid
        self._replay_data = replay_data or {
            "n_gated": 5, "n_published": 3, "n_suppressed": 2,
            "reason_counts": {}, "chain_valid": chain_valid,
        }

    def verify_chain(self) -> bool:
        return self._chain_valid

    def replay(self) -> dict:
        return self._replay_data


# ===========================================================================
# LatencyTracker
# ===========================================================================

class TestLatencyTracker(unittest.TestCase):

    def test_record_single_sample(self):
        t = LatencyTracker()
        t.record("pinnacle", 120.5, True)
        s = t.stats("pinnacle")
        self.assertEqual(s["count"], 1)
        self.assertEqual(s["p50_ms"], 120.5)
        self.assertEqual(s["failure_count"], 0)

    def test_percentiles_five_values(self):
        t = LatencyTracker()
        for v in [10.0, 20.0, 30.0, 40.0, 50.0]:
            t.record("betfair", v, True)
        s = t.stats("betfair")
        self.assertLessEqual(s["p50_ms"], 30.0)
        self.assertGreaterEqual(s["p50_ms"], 20.0)
        self.assertGreaterEqual(s["p95_ms"], s["p50_ms"])
        self.assertGreaterEqual(s["p99_ms"], s["p95_ms"])

    def test_failure_rate_two_of_four(self):
        t = LatencyTracker()
        t.record("p", 10.0, True)
        t.record("p", 20.0, False)
        t.record("p", 30.0, True)
        t.record("p", 40.0, False)
        s = t.stats("p")
        self.assertEqual(s["failure_count"], 2)
        self.assertAlmostEqual(s["failure_rate"], 0.5, places=3)

    def test_rolling_window_respects_max(self):
        t = LatencyTracker(max_samples=3)
        for i in range(10):
            t.record("x", float(i), True)
        s = t.stats("x")
        self.assertEqual(s["count"], 3)

    def test_empty_provider_returns_count_zero(self):
        t = LatencyTracker()
        s = t.stats("nonexistent")
        self.assertEqual(s["count"], 0)
        self.assertNotIn("p50_ms", s)

    def test_all_stats_aggregates_multiple_providers(self):
        t = LatencyTracker()
        t.record("a", 100.0, True)
        t.record("b", 200.0, True)
        all_s = t.all_stats()
        self.assertIn("a", all_s)
        self.assertIn("b", all_s)


# ===========================================================================
# CompletenessTracker
# ===========================================================================

class TestCompletenessTracker(unittest.TestCase):

    def test_all_clean_ratio_is_one(self):
        ct = CompletenessTracker()
        for _ in range(10):
            ct.record_iteration(5, False, False)
        s = ct.stats()
        self.assertEqual(s["clean_ratio"], 1.0)
        self.assertEqual(s["total_jobs"], 50)

    def test_with_errors_reduces_ratio(self):
        ct = CompletenessTracker()
        ct.record_iteration(5, False, False)
        ct.record_iteration(5, True, False)
        s = ct.stats()
        self.assertAlmostEqual(s["clean_ratio"], 0.5, places=3)
        self.assertEqual(s["error_iterations"], 1)

    def test_degraded_iterations_tracked(self):
        ct = CompletenessTracker()
        ct.record_iteration(3, False, True)
        ct.record_iteration(3, False, False)
        s = ct.stats()
        self.assertEqual(s["degraded_iterations"], 1)
        self.assertAlmostEqual(s["degraded_ratio"], 0.5, places=3)

    def test_empty_stats_have_expected_keys(self):
        ct = CompletenessTracker()
        s = ct.stats()
        for key in ("total_iterations", "total_jobs", "error_iterations",
                    "degraded_iterations", "clean_ratio", "degraded_ratio"):
            self.assertIn(key, s)


# ===========================================================================
# TruthConfidenceTracker
# ===========================================================================

class TestTruthConfidenceTracker(unittest.TestCase):

    def test_mean_matches_average(self):
        tc = TruthConfidenceTracker()
        tc.record(0.6)
        tc.record(0.8)
        s = tc.stats()
        self.assertAlmostEqual(s["mean"], 0.7, places=3)
        self.assertEqual(s["count"], 2)

    def test_below_threshold_counted(self):
        tc = TruthConfidenceTracker(below_threshold=0.7)
        tc.record(0.5)
        tc.record(0.6)
        tc.record(0.8)
        s = tc.stats()
        self.assertEqual(s["below_threshold_count"], 2)
        self.assertAlmostEqual(s["below_threshold_rate"], 2 / 3, places=3)

    def test_percentiles_ordered(self):
        tc = TruthConfidenceTracker()
        for v in [0.1, 0.3, 0.5, 0.7, 0.9]:
            tc.record(v)
        s = tc.stats()
        self.assertLessEqual(s["p10"], s["p50"])
        self.assertLessEqual(s["p50"], s["p90"])

    def test_empty_returns_count_zero(self):
        tc = TruthConfidenceTracker()
        s = tc.stats()
        self.assertEqual(s["count"], 0)
        self.assertNotIn("mean", s)


# ===========================================================================
# FeedMonitor
# ===========================================================================

class TestFeedMonitor(unittest.TestCase):

    def test_record_iteration_updates_completeness(self):
        fm = FeedMonitor()
        fm.record_iteration(_FakeSummary(jobs_processed=4))
        s = fm.completeness.stats()
        self.assertEqual(s["total_iterations"], 1)
        self.assertEqual(s["total_jobs"], 4)

    def test_record_iteration_with_signals_updates_tc(self):
        fm = FeedMonitor()
        signals = [_FakeSignal(truth_confidence=0.75), _FakeSignal(truth_confidence=0.85)]
        fm.record_iteration(_FakeSummary(), signals=signals)
        s = fm.truth_confidence.stats()
        self.assertEqual(s["count"], 2)

    def test_record_iteration_error_updates_completeness(self):
        fm = FeedMonitor()
        fm.record_iteration(_FakeSummary(errors=["ingest: timeout"]))
        s = fm.completeness.stats()
        self.assertEqual(s["error_iterations"], 1)

    def test_snapshot_contains_all_keys(self):
        fm = FeedMonitor()
        snap = fm.snapshot()
        self.assertIn("latency", snap)
        self.assertIn("completeness", snap)
        self.assertIn("truth_confidence", snap)


# ===========================================================================
# MonitoringProvider
# ===========================================================================

class TestMonitoringProvider(unittest.TestCase):

    def _make_inner(self, name="pinnacle"):
        return MockOddsProvider(
            name=name,
            provider_class="SHARP",
            odds_fixture={"m1": {"1X2": {"HOME": 2.0, "DRAW": 3.4, "AWAY": 3.8}}},
        )

    def test_name_propagated_from_inner(self):
        inner = self._make_inner("betfair")
        mp = MonitoringProvider(inner, LatencyTracker())
        self.assertEqual(mp.name, "betfair")

    def test_fetch_snapshot_records_success_latency(self):
        tracker = LatencyTracker()
        mp = MonitoringProvider(self._make_inner(), tracker)
        mp.fetch_snapshot("m1", "1X2", "T-24h")
        s = tracker.stats("pinnacle")
        self.assertEqual(s["count"], 1)
        self.assertEqual(s["failure_count"], 0)

    def test_fetch_outcome_records_success_latency(self):
        inner = MockOddsProvider(
            name="p",
            provider_class="SHARP",
            odds_fixture={},
            outcomes={"m1": ProviderOutcome("COMPLETED", 2, 1)},
        )
        tracker = LatencyTracker()
        mp = MonitoringProvider(inner, tracker)
        mp.fetch_outcome("m1")
        s = tracker.stats("p")
        self.assertEqual(s["count"], 1)
        self.assertEqual(s["failure_rate"], 0.0)

    def test_provider_error_records_failure_latency(self):
        inner = MockOddsProvider(
            name="err",
            provider_class="SHARP",
            odds_fixture={},
            fail_matches={"m99"},
        )
        tracker = LatencyTracker()
        mp = MonitoringProvider(inner, tracker)
        with self.assertRaises(ProviderError):
            mp.fetch_snapshot("m99", "1X2", "T-24h")
        s = tracker.stats("err")
        self.assertEqual(s["failure_count"], 1)
        self.assertAlmostEqual(s["failure_rate"], 1.0, places=3)

    def test_proxy_returns_same_quotes_as_inner(self):
        inner = self._make_inner()
        tracker = LatencyTracker()
        mp = MonitoringProvider(inner, tracker)
        direct = inner.fetch_snapshot("m1", "1X2", "T-24h")
        via_proxy = mp.fetch_snapshot("m1", "1X2", "T-24h")
        self.assertEqual(direct, via_proxy)


# ===========================================================================
# SettlementVerifier
# ===========================================================================

class TestSettlementVerifier(unittest.TestCase):

    def test_record_triggered_increments_total(self):
        sv = SettlementVerifier()
        sv.record_triggered(3)
        r = sv.report()
        self.assertEqual(r["total_triggered"], 3)
        self.assertEqual(r["settlement_batches"], 1)

    def test_multiple_batches_accumulate(self):
        sv = SettlementVerifier()
        sv.record_triggered(2)
        sv.record_triggered(1)
        r = sv.report()
        self.assertEqual(r["total_triggered"], 3)
        self.assertEqual(r["settlement_batches"], 2)

    def test_record_zero_is_noop(self):
        sv = SettlementVerifier()
        sv.record_triggered(0)
        r = sv.report()
        self.assertEqual(r["total_triggered"], 0)
        self.assertEqual(r["settlement_batches"], 0)

    def test_report_timestamps_populated(self):
        sv = SettlementVerifier()
        sv.record_triggered(1)
        r = sv.report()
        self.assertIsNotNone(r["first_settlement"])
        self.assertIsNotNone(r["last_settlement"])

    def test_empty_report_has_none_timestamps(self):
        sv = SettlementVerifier()
        r = sv.report()
        self.assertIsNone(r["first_settlement"])
        self.assertIsNone(r["last_settlement"])

    def test_export_json_is_valid(self):
        sv = SettlementVerifier()
        sv.record_triggered(5)
        raw = sv.export_json()
        parsed = json.loads(raw)
        self.assertEqual(parsed["total_triggered"], 5)


# ===========================================================================
# ReplayVerifier
# ===========================================================================

class TestReplayVerifier(unittest.TestCase):

    def test_check_chain_valid_records_true(self):
        gw = _FakeGateway(chain_valid=True)
        rv = ReplayVerifier(gw)
        result = rv.check()
        self.assertTrue(result["chain_valid"])
        self.assertEqual(result["n_gated"], 5)

    def test_check_chain_invalid_records_false(self):
        gw = _FakeGateway(chain_valid=False, replay_data={
            "n_gated": 2, "n_published": 1, "n_suppressed": 1,
            "reason_counts": {}, "chain_valid": False,
        })
        rv = ReplayVerifier(gw)
        result = rv.check()
        self.assertFalse(result["chain_valid"])

    def test_report_no_checks_all_valid_true(self):
        rv = ReplayVerifier(_FakeGateway())
        r = rv.report()
        self.assertEqual(r["checks"], 0)
        self.assertTrue(r["all_valid"])
        self.assertIsNone(r["last_check"])

    def test_report_tracks_failure_count(self):
        gw_bad = _FakeGateway(
            chain_valid=False,
            replay_data={"n_gated": 0, "n_published": 0, "n_suppressed": 0,
                         "reason_counts": {}, "chain_valid": False},
        )
        rv = ReplayVerifier(gw_bad)
        rv.check()
        rv.check()
        r = rv.report()
        self.assertEqual(r["checks"], 2)
        self.assertEqual(r["chain_failures"], 2)
        self.assertFalse(r["all_valid"])

    def test_report_last_check_is_most_recent(self):
        gw = _FakeGateway(chain_valid=True)
        rv = ReplayVerifier(gw)
        rv.check()
        rv.check()
        r = rv.report()
        self.assertEqual(r["checks"], 2)
        self.assertTrue(r["last_check"]["chain_valid"])


# ===========================================================================
# ReadinessScore
# ===========================================================================

def _perfect_monitor_snap():
    return {
        "completeness": {
            "clean_ratio": 1.0,
            "total_iterations": 100,
            "total_jobs": 500,
            "error_iterations": 0,
            "degraded_iterations": 0,
            "degraded_ratio": 0.0,
        },
        "latency": {
            "pinnacle": {
                "provider": "pinnacle",
                "count": 100,
                "p50_ms": 200.0,
                "p95_ms": 500.0,
                "p99_ms": 800.0,
                "mean_ms": 250.0,
                "failure_count": 0,
                "failure_rate": 0.0,
            }
        },
        "truth_confidence": {
            "count": 50,
            "mean": 0.80,
            "p10": 0.65,
            "p50": 0.80,
            "p90": 0.92,
            "below_threshold_count": 3,
            "below_threshold_rate": 0.06,
            "threshold": 0.6,
        },
    }


def _empty_monitor_snap():
    return {
        "completeness": {
            "clean_ratio": 0.0,
            "total_iterations": 0,
            "total_jobs": 0,
            "error_iterations": 0,
            "degraded_iterations": 0,
            "degraded_ratio": 0.0,
        },
        "latency": {},
        "truth_confidence": {"count": 0},
    }


class TestReadinessScore(unittest.TestCase):

    def _perfect(self):
        return ReadinessScore().breakdown(
            _perfect_monitor_snap(),
            {"total_triggered": 10, "settlement_batches": 3,
             "first_settlement": "...", "last_settlement": "..."},
            {"checks": 5, "chain_failures": 0, "all_valid": True, "last_check": {}},
        )

    def test_perfect_inputs_score_100(self):
        b = self._perfect()
        self.assertAlmostEqual(b["overall"], 100.0, delta=0.1)

    def test_go_live_verdict_at_90_plus(self):
        b = self._perfect()
        self.assertEqual(b["verdict"], "GO_LIVE")

    def test_zero_score_verdict_not_ready(self):
        rs = ReadinessScore()
        b = rs.breakdown(
            _empty_monitor_snap(),
            {"total_triggered": 0, "settlement_batches": 0,
             "first_settlement": None, "last_settlement": None},
            {"checks": 3, "chain_failures": 3, "all_valid": False, "last_check": {}},
        )
        self.assertEqual(b["verdict"], "NOT_READY")

    def test_replay_failure_reduces_score(self):
        rs = ReadinessScore()
        b_good = rs.breakdown(
            _perfect_monitor_snap(),
            {"total_triggered": 1, "settlement_batches": 1,
             "first_settlement": ".", "last_settlement": "."},
            {"checks": 1, "chain_failures": 0, "all_valid": True, "last_check": {}},
        )
        b_bad = rs.breakdown(
            _perfect_monitor_snap(),
            {"total_triggered": 1, "settlement_batches": 1,
             "first_settlement": ".", "last_settlement": "."},
            {"checks": 1, "chain_failures": 1, "all_valid": False, "last_check": {}},
        )
        self.assertGreater(b_good["overall"], b_bad["overall"])

    def test_weights_sum_to_one(self):
        total = sum(ReadinessScore.WEIGHTS.values())
        self.assertAlmostEqual(total, 1.0, places=10)

    def test_breakdown_contains_all_keys(self):
        b = self._perfect()
        for key in ("overall", "verdict", "dimensions", "thresholds"):
            self.assertIn(key, b)
        for dim in ("feed_completeness", "provider_latency", "truth_confidence",
                    "settlement_accuracy", "replay_integrity"):
            self.assertIn(dim, b["dimensions"])

    def test_verdict_function_boundaries(self):
        self.assertEqual(_verdict(95.0), "GO_LIVE")
        self.assertEqual(_verdict(90.0), "GO_LIVE")
        self.assertEqual(_verdict(89.9), "CONDITIONAL_GO")
        self.assertEqual(_verdict(70.0), "CONDITIONAL_GO")
        self.assertEqual(_verdict(69.9), "NOT_READY")
        self.assertEqual(_verdict(0.0), "NOT_READY")


# ===========================================================================
# DailySummary
# ===========================================================================

class TestDailySummary(unittest.TestCase):

    def _make_summary(self, ds, day="2026-06-11"):
        return ds.compile(
            day=day,
            monitor_snap=_empty_monitor_snap(),
            settlement_report={"total_triggered": 0},
            replay_report={"checks": 0, "chain_failures": 0, "all_valid": True},
            health_snap_dict={},
            readiness_breakdown={"overall": 85.0, "verdict": "CONDITIONAL_GO",
                                 "dimensions": {}, "thresholds": {}},
        )

    def test_compile_contains_required_keys(self):
        ds = DailySummary()
        summary = self._make_summary(ds)
        for key in ("day", "generated_at", "readiness", "health",
                    "monitoring", "settlement", "replay"):
            self.assertIn(key, summary)
        self.assertEqual(summary["day"], "2026-06-11")

    def test_export_json_creates_readable_file(self):
        ds = DailySummary()
        summary = self._make_summary(ds)
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "2026-06-11.json")
            ds.export_json(path, summary)
            self.assertTrue(os.path.exists(path))
            with open(path) as fh:
                parsed = json.load(fh)
            self.assertEqual(parsed["day"], "2026-06-11")

    def test_export_json_creates_parent_dirs(self):
        ds = DailySummary()
        summary = self._make_summary(ds)
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "nested", "dir", "report.json")
            ds.export_json(path, summary)
            self.assertTrue(os.path.exists(path))


# ===========================================================================
# ValidationHarness
# ===========================================================================

class TestValidationHarness(unittest.TestCase):

    def _make_harness(self, summary=None, tmpdir=None):
        from src.market.service.validation import ValidationHarness
        gw = _FakeGateway()
        runtime = _FakeRuntime(summary=summary)
        harness = ValidationHarness(
            runtime=runtime,
            monitor=FeedMonitor(),
            settlement_verifier=SettlementVerifier(),
            replay_verifier=ReplayVerifier(gw),
            report_dir=tmpdir or "/tmp",
        )
        return harness, runtime

    def test_run_iteration_calls_run_once(self):
        harness, runtime = self._make_harness()
        harness.run_iteration()
        self.assertEqual(runtime.run_once_calls, 1)

    def test_run_iteration_updates_completeness(self):
        harness, _ = self._make_harness(_FakeSummary(jobs_processed=7))
        harness.run_iteration()
        s = harness._monitor.completeness.stats()
        self.assertEqual(s["total_jobs"], 7)

    def test_run_iteration_records_settlements(self):
        harness, _ = self._make_harness(_FakeSummary(outcomes_triggered=3))
        harness.run_iteration()
        r = harness._settlement.report()
        self.assertEqual(r["total_triggered"], 3)

    def test_readiness_score_returns_verdict(self):
        harness, _ = self._make_harness()
        score = harness.readiness_score()
        self.assertIn("verdict", score)
        self.assertIn("overall", score)

    def test_export_daily_summary_creates_file(self):
        with tempfile.TemporaryDirectory() as d:
            harness, _ = self._make_harness(tmpdir=d)
            harness.run_iteration()
            path = harness.export_daily_summary("2026-06-11")
            self.assertTrue(os.path.exists(path))
            with open(path) as fh:
                data = json.load(fh)
            self.assertEqual(data["day"], "2026-06-11")

    def test_shutdown_sets_stop_on_both(self):
        harness, runtime = self._make_harness()
        harness.shutdown()
        self.assertTrue(harness._stop)
        self.assertTrue(runtime._stop)

    def test_run_stops_at_max_iterations(self):
        harness, runtime = self._make_harness()
        harness.run(max_iterations=3)
        self.assertEqual(runtime.run_once_calls, 3)


# ===========================================================================
# make_l4_dry_run_profile
# ===========================================================================

class TestMakeL4Profile(unittest.TestCase):

    def test_dry_run_enabled_by_default(self):
        cfg = make_l4_dry_run_profile()
        self.assertTrue(cfg.telegram.dry_run)

    def test_three_providers_configured(self):
        cfg = make_l4_dry_run_profile()
        names = [p.name for p in cfg.providers]
        self.assertIn("pinnacle", names)
        self.assertIn("betfair", names)
        self.assertIn("betfair_outcome", names)

    def test_all_providers_enabled(self):
        cfg = make_l4_dry_run_profile()
        for p in cfg.providers:
            self.assertTrue(p.enabled, f"provider {p.name!r} should be enabled")

    def test_config_validates_successfully(self):
        cfg = make_l4_dry_run_profile()
        cfg.validate()  # should not raise

    def test_custom_db_paths_applied(self):
        cfg = make_l4_dry_run_profile(
            scheduler_db="s.db", truth_db="t.db",
            control_db="c.db", bridge_db="b.db",
        )
        self.assertEqual(cfg.scheduler.db_path, "s.db")
        self.assertEqual(cfg.truth_db_path, "t.db")
        self.assertEqual(cfg.control_db_path, "c.db")
        self.assertEqual(cfg.bridge_db_path, "b.db")

    def test_poll_interval_applied(self):
        cfg = make_l4_dry_run_profile(poll_interval_seconds=60.0)
        self.assertEqual(cfg.scheduler.poll_interval_seconds, 60.0)


# ===========================================================================
# Additivity — acceptance hash must remain unchanged
# ===========================================================================

class TestAdditivity(unittest.TestCase):

    def test_m11_acceptance_hash_unchanged(self):
        import tests.test_m11_acceptance as m
        self.assertEqual(
            m.run_hash(m.baseline_providers()),
            m.TestM11Acceptance.BASELINE_HASH,
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
