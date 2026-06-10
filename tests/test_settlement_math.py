"""M8.3 settlement math & error decomposition invariants. No network, no betting."""

import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

from src.market.settlement import (
    SettlementMathEngine, MetricStatus, settlement_confidence,
    SettlementLedger, ClosureLedger, MatchOutcome,
)
from src.market.truth import TruthStore, RawSnapshot

KO = datetime(2026, 2, 1, 18, 0, tzinfo=timezone.utc)


def fin(eng, mid="b1", entry=2.10, o_close=1.95, p_close=0.50, p_model=0.58,
        result="WON", y=1, oc=1.0, cc=0.9, tc=0.85, stale=False):
    return eng.finalize(mid, "m", "1X2", "HOME", entry, result=result, y=y,
                        o_close=o_close, p_close=p_close, p_model=p_model,
                        outcome_conf=oc, close_conf=cc, truth_conf=tc, is_stale=stale)


class TestMetricCorrectness(unittest.TestCase):
    def setUp(self):
        self.eng = SettlementMathEngine(":memory:")

    def tearDown(self):
        self.eng.close()

    def test_roi_won(self):
        r = fin(self.eng, result="WON", y=1, entry=2.10)
        self.assertAlmostEqual(r.realized_roi, 1.10, places=9)

    def test_roi_lost(self):
        r = fin(self.eng, result="LOST", y=0)
        self.assertAlmostEqual(r.realized_roi, -1.0, places=9)

    def test_clv(self):
        r = fin(self.eng, entry=2.10, o_close=1.95)
        self.assertAlmostEqual(r.realized_clv, 2.10 / 1.95 - 1.0, places=6)  # ledger rounds to 6

    def test_status_completed(self):
        self.assertEqual(fin(self.eng).status, MetricStatus.COMPLETED.value)


class TestDecomposition(unittest.TestCase):
    def setUp(self):
        self.eng = SettlementMathEngine(":memory:")

    def tearDown(self):
        self.eng.close()

    def test_decomposition_balances(self):
        r = fin(self.eng, entry=2.0, o_close=2.5, p_close=0.40, p_model=0.58, y=1)
        # residual ~ 0
        self.assertAlmostEqual(r.residual, 0.0, places=9)
        # components sum to total error p_model - y
        total = 0.58 - 1
        self.assertAlmostEqual(r.calibration_error + r.execution_error + r.truth_error,
                               total, places=9)

    def test_component_values(self):
        r = fin(self.eng, entry=2.0, o_close=2.5, p_close=0.40, p_model=0.58, y=0)
        p_entry = 1 / 2.0
        self.assertAlmostEqual(r.calibration_error, 0.58 - p_entry, places=9)
        self.assertAlmostEqual(r.execution_error, p_entry - 0.40, places=9)
        self.assertAlmostEqual(r.truth_error, 0.40 - 0, places=9)

    def test_no_decomposition_without_close(self):
        r = fin(self.eng, o_close=None, p_close=None)
        self.assertIsNone(r.calibration_error)
        self.assertIsNone(r.residual)


class TestConfidence(unittest.TestCase):
    def test_monotonic_in_each_component(self):
        lo = settlement_confidence(0.5, 0.5, 0.5)
        self.assertGreater(settlement_confidence(0.9, 0.5, 0.5), lo)   # outcome up
        self.assertGreater(settlement_confidence(0.5, 0.9, 0.5), lo)   # close up
        self.assertGreater(settlement_confidence(0.5, 0.5, 0.9), lo)   # truth up

    def test_stale_degrades(self):
        self.assertLess(settlement_confidence(0.9, 0.9, 0.9, is_stale=True),
                        settlement_confidence(0.9, 0.9, 0.9, is_stale=False))

    def test_missing_close_lowers_confidence(self):
        eng = SettlementMathEngine(":memory:")
        with_close = fin(eng, mid="a", cc=0.9).settlement_confidence
        without = fin(eng, mid="b", o_close=None, p_close=None, cc=0.9).settlement_confidence
        self.assertLess(without, with_close)   # close_conf forced to 0 when missing
        eng.close()

    def test_bounded(self):
        self.assertLessEqual(settlement_confidence(1, 1, 1), 1.0)
        self.assertGreaterEqual(settlement_confidence(0, 0, 0), 0.0)


class TestValidation(unittest.TestCase):
    def setUp(self):
        self.eng = SettlementMathEngine(":memory:")

    def tearDown(self):
        self.eng.close()

    def test_missing_outcome_pending(self):
        r = fin(self.eng, result=None, y=None)
        self.assertEqual(r.status, MetricStatus.MISSING_OUTCOME.value)
        self.assertIsNone(r.realized_roi)

    def test_missing_close_status(self):
        r = fin(self.eng, o_close=None, p_close=None)
        self.assertEqual(r.status, MetricStatus.MISSING_CLOSE.value)
        self.assertIsNotNone(r.realized_roi)     # ROI still computable from outcome
        self.assertIsNone(r.realized_clv)

    def test_void_handling(self):
        r = fin(self.eng, result="VOID", y=None)
        self.assertEqual(r.status, MetricStatus.VOID.value)
        self.assertTrue(r.is_void)
        self.assertEqual(r.realized_roi, 0.0)

    def test_idempotent_finalize(self):
        a = fin(self.eng, mid="x")
        b = fin(self.eng, mid="x")
        self.assertEqual(a.entry_hash, b.entry_hash)
        self.assertEqual(self.eng.replay().n_metrics, 1)


class TestRollingAndReplay(unittest.TestCase):
    def _populate(self, eng):
        fin(eng, mid="b1", entry=2.0, o_close=1.9, result="WON", y=1)     # roi +1.0
        fin(eng, mid="b2", entry=3.0, o_close=3.2, result="LOST", y=0)    # roi -1.0
        fin(eng, mid="b3", entry=2.5, o_close=2.4, result="WON", y=1)     # roi +1.5
        fin(eng, mid="v1", entry=2.0, o_close=1.9, result="VOID", y=None) # excluded

    def test_rolling(self):
        eng = SettlementMathEngine(":memory:")
        self._populate(eng)
        roll = eng.rolling(window=100)
        # moving roi over non-void = (1.0 - 1.0 + 1.5)/3
        self.assertAlmostEqual(roll.moving_roi, 0.5, places=6)
        self.assertIsNotNone(roll.conf_weighted_clv)
        eng.close()

    def test_replay_deterministic(self):
        eng = SettlementMathEngine(":memory:")
        self._populate(eng)
        self.assertEqual(eng.replay().to_dict(), eng.replay().to_dict())
        s = eng.replay()
        self.assertTrue(s.chain_valid)
        self.assertEqual(s.n_void, 1)
        self.assertLess(s.mean_abs_residual, 1e-9)   # decomposition balances
        eng.close()

    def test_chain_tamper_detected(self):
        eng = SettlementMathEngine(":memory:")
        self._populate(eng)
        eng.conn.execute("UPDATE settlement_metrics SET realized_roi=42 WHERE seq=1")
        eng.conn.commit()
        self.assertFalse(eng.verify_chain())
        eng.close()

    def test_replay_safe_reopen(self):
        path = os.path.join(tempfile.mkdtemp(), "metrics.db")
        try:
            eng = SettlementMathEngine(path)
            self._populate(eng)
            s1 = eng.replay().to_dict()
            eng.close()
            eng2 = SettlementMathEngine(path)
            self.assertEqual(eng2.replay().to_dict(), s1)
            eng2.close()
        finally:
            if os.path.exists(path):
                os.remove(path)


class TestIntegrationWithLedgers(unittest.TestCase):
    def test_finalize_from_m81_and_m82(self):
        # M2 truth -> M8.2 lock close; M8.1 outcome; M8.3 connects them
        store = TruthStore(":memory:")
        for sel, p in {"HOME": 0.52, "DRAW": 0.27, "AWAY": 0.21}.items():
            store.ingest_snapshot(RawSnapshot("m", "pinnacle", "1X2", sel,
                                              round(1.0 / p, 4), "CLOSE", KO - timedelta(minutes=2)))
        store.recompute_truth("m", "1X2", "CLOSE")
        closure = ClosureLedger(":memory:")
        closure.lock(store, "m", "1X2", KO)
        settle = SettlementLedger(":memory:")
        settle.ingest_outcome(MatchOutcome("m", "COMPLETED", 2, 1))   # HOME win

        eng = SettlementMathEngine(":memory:")
        rec = eng.finalize_from_ledgers(settle, closure, bet_id="bet1", match_id="m",
                                        market="1X2", selection="HOME",
                                        entry_odds=2.20, p_model=0.58, truth_conf=0.8)
        self.assertEqual(rec.result, "WON")
        self.assertEqual(rec.status, MetricStatus.COMPLETED.value)
        self.assertAlmostEqual(rec.realized_roi, 1.20, places=9)
        # CLV uses the LOCKED close from M8.2, not an arbitrary value
        self.assertIsNotNone(rec.realized_clv)
        self.assertIsNotNone(rec.calibration_error)
        store.close(); closure.close(); settle.close(); eng.close()

    def test_finalize_from_ledgers_pending_without_outcome(self):
        store = TruthStore(":memory:")
        for sel, p in {"HOME": 0.5, "DRAW": 0.3, "AWAY": 0.2}.items():
            store.ingest_snapshot(RawSnapshot("m", "pinnacle", "1X2", sel,
                                              round(1.0 / p, 4), "CLOSE", KO - timedelta(minutes=2)))
        store.recompute_truth("m", "1X2", "CLOSE")
        closure = ClosureLedger(":memory:"); closure.lock(store, "m", "1X2", KO)
        settle = SettlementLedger(":memory:")   # NO outcome ingested
        eng = SettlementMathEngine(":memory:")
        rec = eng.finalize_from_ledgers(settle, closure, bet_id="b", match_id="m",
                                        market="1X2", selection="HOME",
                                        entry_odds=2.0, p_model=0.55, truth_conf=0.8)
        self.assertEqual(rec.status, MetricStatus.MISSING_OUTCOME.value)
        store.close(); closure.close(); settle.close(); eng.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
