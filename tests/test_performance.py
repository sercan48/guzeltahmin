"""M8.4 performance finalization & attribution invariants. No network, no betting."""

import os
import tempfile
import unittest

from src.market.settlement import (
    PerformanceAggregator, PerformanceEntry, SettlementMathEngine,
)


def entry(mid, league="EPL", market="1X2", regime="EFF_STABLE", tier="TIER_A",
          src="SHARP", roi=1.0, clv=0.05, conf=0.9, void=False, status="COMPLETED"):
    return PerformanceEntry(mid, "m_" + mid, league, market, "HOME", regime, tier, src,
                            None if void else roi, None if void else clv, conf,
                            void, status)


def populate(agg):
    agg.ingest(entry("1", league="EPL", src="SHARP", roi=1.0, clv=0.06, tier="TIER_A"))
    agg.ingest(entry("2", league="EPL", src="SOFT", roi=-1.0, clv=-0.02, tier="TIER_B"))
    agg.ingest(entry("3", league="LaLiga", src="SHARP", roi=1.5, clv=0.04, tier="TIER_A"))
    agg.ingest(entry("4", league="LaLiga", src="SOFT", roi=-1.0, clv=-0.05, tier="TIER_C"))
    agg.ingest(entry("v", league="EPL", src="MIXED", void=True))   # void: excluded from roi/clv


class TestAggregation(unittest.TestCase):
    def setUp(self):
        self.agg = PerformanceAggregator(":memory:")
        populate(self.agg)

    def tearDown(self):
        self.agg.close()

    def test_global_totals(self):
        g = self.agg.global_summary()
        self.assertEqual(g.n, 5)
        self.assertEqual(g.n_void, 1)
        # roi total over non-void = 1.0 -1.0 +1.5 -1.0 = 0.5
        self.assertAlmostEqual(g.roi_total, 0.5, places=6)

    def test_idempotent_ingest(self):
        self.assertFalse(self.agg.ingest(entry("1")))   # duplicate metric_id
        self.assertEqual(self.agg.global_summary().n, 5)

    def test_weighted_metric_correctness(self):
        g = self.agg.global_summary()
        # conf-weighted roi = sum(conf*roi)/sum(conf) over non-void (all conf 0.9 here)
        self.assertAlmostEqual(g.conf_weighted_roi, g.roi_mean, places=6)


class TestSegmentation(unittest.TestCase):
    def setUp(self):
        self.agg = PerformanceAggregator(":memory:")
        populate(self.agg)

    def tearDown(self):
        self.agg.close()

    def test_segment_totals_reconcile_to_global(self):
        g = self.agg.global_summary()
        for by in ("league", "market", "regime", "tier", "source_class"):
            segs = self.agg.segment(by)
            self.assertAlmostEqual(sum(s.roi_total for s in segs.values()), g.roi_total, places=6)
            self.assertEqual(sum(s.n for s in segs.values()), g.n)
            self.assertEqual(sum(s.n_void for s in segs.values()), g.n_void)

    def test_league_breakdown(self):
        segs = self.agg.segment("league")
        self.assertEqual(set(segs), {"EPL", "LaLiga"})
        self.assertEqual(segs["LaLiga"].n, 2)

    def test_tier_breakdown(self):
        segs = self.agg.segment("tier")
        self.assertEqual(set(segs), {"TIER_A", "TIER_B", "TIER_C"})

    def test_unknown_segment_raises(self):
        with self.assertRaises(ValueError):
            self.agg.segment("nonsense")


class TestAttribution(unittest.TestCase):
    def test_sharp_vs_soft(self):
        agg = PerformanceAggregator(":memory:")
        populate(agg)
        rep = agg.sharp_vs_soft()
        self.assertEqual(rep.sharp.n, 2)
        self.assertEqual(rep.soft.n, 2)
        # sharp roi mean (1.0,1.5)=1.25 ; soft (-1,-1)=-1 ; delta +2.25
        self.assertAlmostEqual(rep.sharp.roi_mean, 1.25, places=6)
        self.assertAlmostEqual(rep.soft.roi_mean, -1.0, places=6)
        self.assertAlmostEqual(rep.roi_delta, 2.25, places=6)
        self.assertGreater(rep.clv_delta, 0)   # sharp beats close more than soft
        agg.close()


class TestRollingAndStability(unittest.TestCase):
    def test_rolling_window(self):
        agg = PerformanceAggregator(":memory:")
        for i in range(5):
            agg.ingest(entry(str(i), roi=float(i), clv=0.01 * i))
        roll = agg.rolling(window=2)   # last 2 entries: roi 3,4 -> mean 3.5
        self.assertEqual(roll.n, 2)
        self.assertAlmostEqual(roll.roi_mean, 3.5, places=6)
        agg.close()

    def test_stability_bounds_and_sample_flag(self):
        agg = PerformanceAggregator(":memory:")
        populate(agg)
        st = agg.stability()
        for v in (st.clv_stability, st.roi_stability, st.confidence_stability):
            if v is not None:
                self.assertGreaterEqual(v, 0.0)
                self.assertLessEqual(v, 1.0)
        self.assertFalse(st.sample_sufficient)   # only 4 non-void < 30
        agg.close()

    def test_constant_series_max_stability(self):
        agg = PerformanceAggregator(":memory:")
        for i in range(5):
            agg.ingest(entry(str(i), roi=1.0, clv=0.05, conf=0.9))
        st = agg.stability()
        self.assertEqual(st.roi_stability, 1.0)   # zero variance -> full stability
        self.assertEqual(st.clv_stability, 1.0)
        agg.close()


class TestReplayDeterminism(unittest.TestCase):
    def test_replay_deterministic(self):
        agg = PerformanceAggregator(":memory:")
        populate(agg)
        self.assertEqual(agg.replay(), agg.replay())
        agg.close()

    def test_replay_safe_reopen(self):
        path = os.path.join(tempfile.mkdtemp(), "perf.db")
        try:
            agg = PerformanceAggregator(path)
            populate(agg)
            r1 = agg.replay()
            agg.close()
            agg2 = PerformanceAggregator(path)
            self.assertEqual(agg2.replay(), r1)
            agg2.close()
        finally:
            if os.path.exists(path):
                os.remove(path)

    def test_ingest_from_m83_metric(self):
        eng = SettlementMathEngine(":memory:")
        m = eng.finalize("b1", "m", "1X2", "HOME", 2.10, result="WON", y=1,
                         o_close=1.95, p_close=0.50, p_model=0.58,
                         outcome_conf=1.0, close_conf=0.9, truth_conf=0.85)
        agg = PerformanceAggregator(":memory:")
        self.assertTrue(agg.ingest_from_metric(m, league="EPL", regime="EFF_STABLE",
                                               tier="TIER_A", source_class="SHARP"))
        g = agg.global_summary()
        self.assertEqual(g.n, 1)
        self.assertAlmostEqual(g.roi_total, 1.10, places=6)
        eng.close(); agg.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
