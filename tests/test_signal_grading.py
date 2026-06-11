"""M8.5 signal-to-outcome grading invariants. No network, no betting."""

import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

from src.market.truth import TruthStore, RawSnapshot
from src.market.orchestration import PaperSignal
from src.market.settlement import (
    SignalGradingEngine, SignalInput, HitMiss, MetricStatus,
    SettlementLedger, ClosureLedger, SettlementMathEngine, MatchOutcome,
)

KO = datetime(2026, 2, 1, 18, 0, tzinfo=timezone.utc)


def build_settlement(match_id="m", home=2, away=1, status="COMPLETED",
                     close_probs=None):
    """M2 truth -> M8.2 lock; M8.1 outcome; M8.3 engine. Returns the 3 ledgers."""
    store = TruthStore(":memory:")
    close_probs = close_probs or {"HOME": 0.52, "DRAW": 0.27, "AWAY": 0.21}
    for sel, p in close_probs.items():
        store.ingest_snapshot(RawSnapshot(match_id, "pinnacle", "1X2", sel,
                                          round(1.0 / p, 4), "CLOSE", KO - timedelta(minutes=2)))
    store.recompute_truth(match_id, "1X2", "CLOSE")
    closure = ClosureLedger(":memory:")
    closure.lock(store, match_id, "1X2", KO)
    settle = SettlementLedger(":memory:")
    settle.ingest_outcome(MatchOutcome(match_id, status, home, away))
    math = SettlementMathEngine(":memory:")
    store.close()
    return settle, closure, math


def sig(signal_id="s1", match_id="m", selection="HOME", tier="TIER_A", edge=0.07,
        entry=2.20, pred_clv=0.05, p_model=0.58, state="ACTIVE"):
    return SignalInput(signal_id, match_id, "1X2", selection, tier, edge, entry, 0.85,
                       predicted_clv=pred_clv, p_model=p_model, emitted_state=state,
                       league="EPL", regime="EFF_STABLE", source_class="SHARP")


class TestMatchingAndGrading(unittest.TestCase):
    def test_register_idempotent(self):
        eng = SignalGradingEngine(":memory:")
        self.assertTrue(eng.register(sig()))
        self.assertFalse(eng.register(sig()))   # duplicate signal_id
        eng.close()

    def test_grade_win(self):
        settle, closure, math = build_settlement(home=2, away=1)  # HOME win
        eng = SignalGradingEngine(":memory:")
        eng.register(sig(entry=2.20))
        g = eng.grade("s1", settle, closure, math)
        self.assertEqual(g.hit_miss, HitMiss.HIT.value)
        self.assertAlmostEqual(g.realized_roi, 1.20, places=6)
        self.assertIsNotNone(g.realized_clv)
        eng.close()

    def test_grade_loss(self):
        settle, closure, math = build_settlement(home=0, away=2)  # HOME loses
        eng = SignalGradingEngine(":memory:")
        eng.register(sig(selection="HOME", entry=2.20))
        g = eng.grade("s1", settle, closure, math)
        self.assertEqual(g.hit_miss, HitMiss.MISS.value)
        self.assertAlmostEqual(g.realized_roi, -1.0, places=6)
        eng.close()

    def test_void_handling(self):
        settle, closure, math = build_settlement(status="CANCELLED", home=0, away=0)
        eng = SignalGradingEngine(":memory:")
        eng.register(sig())
        g = eng.grade("s1", settle, closure, math)
        self.assertEqual(g.hit_miss, HitMiss.VOID.value)
        self.assertEqual(g.realized_roi, 0.0)
        eng.close()

    def test_clv_accuracy(self):
        settle, closure, math = build_settlement()
        eng = SignalGradingEngine(":memory:")
        eng.register(sig(pred_clv=0.05, entry=2.20))
        g = eng.grade("s1", settle, closure, math)
        # clv_error = realized - predicted ; sign agreement recorded
        self.assertIsNotNone(g.clv_error)
        self.assertIn(g.clv_sign_agree, (0, 1))
        eng.close()

    def test_pending_without_outcome(self):
        store = TruthStore(":memory:")
        for sel, p in {"HOME": 0.5, "DRAW": 0.3, "AWAY": 0.2}.items():
            store.ingest_snapshot(RawSnapshot("m", "pinnacle", "1X2", sel,
                                              round(1.0 / p, 4), "CLOSE", KO - timedelta(minutes=2)))
        store.recompute_truth("m", "1X2", "CLOSE")
        closure = ClosureLedger(":memory:"); closure.lock(store, "m", "1X2", KO)
        settle = SettlementLedger(":memory:")   # no outcome
        math = SettlementMathEngine(":memory:")
        eng = SignalGradingEngine(":memory:")
        eng.register(sig())
        g = eng.grade("s1", settle, closure, math)
        self.assertEqual(g.status, MetricStatus.MISSING_OUTCOME.value)
        self.assertEqual(g.hit_miss, HitMiss.PENDING.value)
        # pending grade is NOT persisted -> portfolio empty
        self.assertEqual(eng.portfolio_summary().n, 0)
        store.close(); eng.close()

    def test_grade_unregistered_returns_none(self):
        settle, closure, math = build_settlement()
        eng = SignalGradingEngine(":memory:")
        self.assertIsNone(eng.grade("ghost", settle, closure, math))
        eng.close()


class TestNoLeakage(unittest.TestCase):
    def test_idempotent_grade_immutable(self):
        settle, closure, math = build_settlement(home=2, away=1)
        eng = SignalGradingEngine(":memory:")
        eng.register(sig(entry=2.20))
        first = eng.grade("s1", settle, closure, math)
        # a LATER outcome change must NOT rewrite the finalized grade
        settle.conn.execute("UPDATE match_outcomes SET home_goals=0, away_goals=3")
        settle.conn.commit()
        second = eng.grade("s1", settle, closure, math)
        self.assertEqual(first.to_dict(), second.to_dict())   # immutable
        eng.close()

    def test_emission_fields_independent_of_outcome(self):
        settle, closure, math = build_settlement(home=0, away=3)  # HOME loses
        eng = SignalGradingEngine(":memory:")
        eng.register(sig(edge=0.07, pred_clv=0.05))
        g = eng.grade("s1", settle, closure, math)
        # emission-time fields are exactly what was registered, regardless of result
        self.assertEqual(g.edge_score, 0.07)
        self.assertEqual(g.predicted_clv, 0.05)
        eng.close()

    def test_signal_graded_only_against_own_match(self):
        # two matches; grading signal for m1 uses only m1's outcome
        s1, c1, mth = build_settlement(match_id="m1", home=2, away=0)  # m1 HOME win
        eng = SignalGradingEngine(":memory:")
        eng.register(sig(signal_id="sa", match_id="m1", entry=2.0))
        g = eng.grade("sa", s1, c1, mth)
        self.assertEqual(g.hit_miss, HitMiss.HIT.value)
        eng.close()


class TestAggregationAndReports(unittest.TestCase):
    def _populate(self, eng):
        # 3 signals across 3 matches, mixed results, different tiers/states
        for sid, (mid, h, a, tier, state) in {
            "s1": ("m1", 2, 0, "TIER_A", "ACTIVE"),
            "s2": ("m2", 0, 1, "TIER_B", "PREMATCH"),
            "s3": ("m3", 1, 0, "TIER_A", "ACTIVE"),
        }.items():
            settle, closure, math = build_settlement(match_id=mid, home=h, away=a)
            eng.register(sig(signal_id=sid, match_id=mid, tier=tier, state=state, entry=2.0))
            eng.grade(sid, settle, closure, math)

    def test_portfolio_summary(self):
        eng = SignalGradingEngine(":memory:")
        self._populate(eng)
        p = eng.portfolio_summary()
        self.assertEqual(p.n, 3)
        self.assertEqual(p.n_hit, 2)              # s1, s3 win; s2 loses
        self.assertAlmostEqual(p.hit_rate, 2 / 3, places=6)
        # roi = +1.0 (s1) -1.0 (s2) +1.0 (s3) = +1.0
        self.assertAlmostEqual(p.roi_total, 1.0, places=6)
        eng.close()

    def test_tier_and_lifecycle_reconcile(self):
        eng = SignalGradingEngine(":memory:")
        self._populate(eng)
        p = eng.portfolio_summary()
        tiers = eng.by_tier()
        life = eng.lifecycle_report()
        self.assertAlmostEqual(sum(s.roi_total for s in tiers.values()), p.roi_total, places=6)
        self.assertEqual(sum(s.n for s in tiers.values()), p.n)
        self.assertEqual(sum(s.n for s in life.values()), p.n)
        self.assertEqual(set(life), {"ACTIVE", "PREMATCH"})
        eng.close()

    def test_calibration_report_measurement_only(self):
        eng = SignalGradingEngine(":memory:")
        self._populate(eng)
        c = eng.calibration_report()
        self.assertEqual(c.n, 3)
        self.assertIsNotNone(c.mean_calibration_error)
        self.assertIsNotNone(c.mean_abs_calibration_error)
        eng.close()


class TestDeterminism(unittest.TestCase):
    def _populate(self, eng):
        for sid, (mid, h, a) in {"s1": ("m1", 2, 0), "s2": ("m2", 0, 1)}.items():
            settle, closure, math = build_settlement(match_id=mid, home=h, away=a)
            eng.register(sig(signal_id=sid, match_id=mid, entry=2.0))
            eng.grade(sid, settle, closure, math)

    def test_replay_deterministic(self):
        eng = SignalGradingEngine(":memory:")
        self._populate(eng)
        self.assertEqual(eng.replay(), eng.replay())
        eng.close()

    def test_replay_safe_reopen(self):
        path = os.path.join(tempfile.mkdtemp(), "grade.db")
        try:
            eng = SignalGradingEngine(path)
            self._populate(eng)
            r1 = eng.replay()
            eng.close()
            eng2 = SignalGradingEngine(path)
            self.assertEqual(eng2.replay(), r1)
            eng2.close()
        finally:
            if os.path.exists(path):
                os.remove(path)

    def test_from_paper(self):
        ps = PaperSignal("m", "1X2", "HOME", edge_score=0.07, tier="TIER_A",
                         confidence=0.82, truth_confidence=0.85,
                         timestamp="2026-02-01T17:00:00+00:00")
        si = SignalInput.from_paper(ps, signal_id="sx", entry_odds=2.2,
                                    predicted_clv=0.05, p_model=0.58)
        self.assertEqual(si.match_id, "m")
        self.assertEqual(si.edge_score, 0.07)
        self.assertEqual(si.entry_odds, 2.2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
