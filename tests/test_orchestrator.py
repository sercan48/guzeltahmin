"""M5 pipeline orchestrator & paper activation invariants. No network, no betting."""

import unittest
from dataclasses import fields
from datetime import datetime, timedelta, timezone

from src.market.edge import SegmentMeta
from src.market.truth import TruthStore
from src.market.orchestration import (
    PipelineOrchestrator, LifecycleService, Trigger, TriggerType, PaperSignal,
    OrchestratorConfig,
)

KO = datetime(2026, 5, 1, 18, 0, tzinfo=timezone.utc)

# HOME shortens (sharp backing); model likes HOME -> backable, confirmed edge.
PATH = [
    (48.0, "OPEN",  {"HOME": 2.20, "DRAW": 3.40, "AWAY": 3.30}),
    (24.0, "T-24h", {"HOME": 2.05, "DRAW": 3.50, "AWAY": 3.55}),
    (1.0,  "T-1h",  {"HOME": 1.95, "DRAW": 3.60, "AWAY": 3.80}),
]
MODEL_PROBS = {"HOME": 0.62, "DRAW": 0.24, "AWAY": 0.14}


def model_provider(match_id, market):
    return dict(MODEL_PROBS)


def seg_provider(match_id):
    return SegmentMeta(calibration_quality=0.85, clv_alignment=0.65)


def _odds_trigger(match_id, hours, stype, probs, key):
    snaps = []
    for bk in ("pinnacle", "bet365"):
        for sel, o in probs.items():
            snaps.append({"provider": bk, "market": "1X2", "selection": sel,
                          "odds": o, "snapshot_type": stype})
    return Trigger(match_id, TriggerType.ODDS_UPDATED, key, KO - timedelta(hours=hours),
                   {"snapshots": snaps})


def make_orch():
    store = TruthStore(":memory:")
    svc = LifecycleService()
    return PipelineOrchestrator(store, svc, model_provider, seg_provider,
                                OrchestratorConfig(active_min_tier="TIER_C"))


def drive_full(orch, match_id="m"):
    orch.handle_trigger(Trigger(match_id, TriggerType.MATCH_CREATED, "c", KO - timedelta(hours=72),
                                {"kickoff": KO.isoformat()}))
    results = []
    for i, (hours, stype, probs) in enumerate(PATH):
        results.append(orch.handle_trigger(_odds_trigger(match_id, hours, stype, probs, f"odds{i}")))
    return results


class TestTriggerRouting(unittest.TestCase):
    def test_match_created_to_prematch(self):
        orch = make_orch()
        r = orch.handle_trigger(Trigger("m", TriggerType.MATCH_CREATED, "c", KO,
                                        {"kickoff": KO.isoformat()}))
        self.assertEqual(r.state, "PREMATCH")

    def test_odds_to_active(self):
        orch = make_orch()
        drive_full(orch)
        self.assertEqual(orch.lifecycle.get("m").state.value, "ACTIVE")

    def test_started_routes_through_locked_to_closed(self):
        orch = make_orch()
        drive_full(orch)
        r = orch.handle_trigger(Trigger("m", TriggerType.MATCH_STARTED, "st", KO))
        self.assertEqual(r.state, "CLOSED")


class TestStateGating(unittest.TestCase):
    def test_signals_generated_in_active(self):
        orch = make_orch()
        results = drive_full(orch)
        total = sum(len(r.signals) for r in results)
        self.assertGreater(total, 0)
        self.assertGreater(orch.metrics.signals_generated, 0)

    def test_signals_blocked_after_kickoff(self):
        orch = make_orch()
        drive_full(orch)
        orch.handle_trigger(Trigger("m", TriggerType.MATCH_STARTED, "st", KO))  # -> CLOSED
        before = orch.metrics.signals_generated
        r = orch.handle_trigger(_odds_trigger("m", 0.0, "T-1h", PATH[-1][2], "late"))
        self.assertTrue(r.blocked)
        self.assertEqual(orch.metrics.signals_generated, before)
        self.assertGreaterEqual(orch.metrics.signals_blocked, 1)


class TestPaperSignal(unittest.TestCase):
    REQUIRED = {"match_id", "market", "selection", "edge_score", "tier",
                "confidence", "truth_confidence", "timestamp"}

    def test_paper_signal_fields_exact(self):
        # PaperSignal must carry exactly the paper fields: NO bankroll/Kelly/stake
        self.assertEqual({f.name for f in fields(PaperSignal)}, self.REQUIRED)

    def test_paper_signal_populated(self):
        orch = make_orch()
        drive_full(orch)
        self.assertTrue(orch.paper_signals)
        s = orch.paper_signals[0]
        self.assertEqual(s.market, "1X2")
        self.assertNotEqual(s.tier, "REJECT")
        self.assertGreater(s.edge_score, 0.0)
        self.assertGreaterEqual(s.truth_confidence, 0.0)
        self.assertNotIn("stake", s.to_dict())
        self.assertNotIn("bankroll", s.to_dict())


class TestExecutionModel(unittest.TestCase):
    def test_replay_determinism(self):
        a, b = make_orch(), make_orch()
        drive_full(a); drive_full(b)
        self.assertEqual([s.to_dict() for s in a.paper_signals],
                         [s.to_dict() for s in b.paper_signals])
        self.assertEqual(a.metrics.signals_generated, b.metrics.signals_generated)

    def test_duplicate_trigger_safe(self):
        orch = make_orch()
        drive_full(orch)
        n_before = len(orch.paper_signals)
        # re-send the last odds trigger verbatim (same idempotency_key)
        orch.handle_trigger(_odds_trigger("m", 1.0, "T-1h", PATH[-1][2], "odds2"))
        self.assertEqual(len(orch.paper_signals), n_before)        # no new signals
        self.assertGreaterEqual(orch.metrics.duplicate_signals, 1)

    def test_metrics_tracked(self):
        orch = make_orch()
        drive_full(orch)
        m = orch.metrics.to_dict()
        for f in ("signals_generated", "signals_blocked", "duplicate_signals",
                  "pipeline_failures", "execution_latency_total"):
            self.assertIn(f, m)
        self.assertGreaterEqual(orch.metrics.execution_latency_total, 0.0)


class TestFailureHandling(unittest.TestCase):
    def test_no_context_graceful(self):
        # odds trigger without a prior MATCH_CREATED (no kickoff) -> no crash, no signals
        orch = make_orch()
        r = orch.handle_trigger(_odds_trigger("ghost", 1.0, "T-1h", PATH[-1][2], "x"))
        self.assertFalse(r.failed)
        self.assertEqual(r.signals, [])

    def test_partial_truth_data_graceful(self):
        # a single-book partial market (missing a selection) -> de-vig skips it; no crash
        orch = make_orch()
        orch.handle_trigger(Trigger("m", TriggerType.MATCH_CREATED, "c", KO - timedelta(hours=72),
                                    {"kickoff": KO.isoformat()}))
        trig = Trigger("m", TriggerType.ODDS_UPDATED, "p", KO - timedelta(hours=1),
                       {"snapshots": [{"provider": "pinnacle", "market": "1X2",
                                       "selection": "HOME", "odds": 1.9, "snapshot_type": "T-1h"}]})
        r = orch.handle_trigger(trig)
        self.assertFalse(r.failed)


if __name__ == "__main__":
    unittest.main(verbosity=2)
