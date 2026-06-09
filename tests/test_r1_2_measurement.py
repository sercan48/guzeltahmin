"""R1.2 measurement-layer invariants (pure, no network, no ML).

Validates the contracts that matter for a *measurement* layer: no leakage,
strict ordering, correct drift/CLV signs, de-vig sanity, and that the integrity
layer catches every intentionally-corrupted row in the fixture.
"""

import math
import unittest

from src.market.fixtures import build_fixture
from src.market.measurement_pipeline import MeasurementPipeline
from src.market.schema import Horizon, OddsRecord
from src.market.timeseries import MarketTimeSeriesBuilder


class TestMeasurementLayer(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.records, cls.contexts = build_fixture()
        cls.result = MeasurementPipeline().run(cls.records, cls.contexts)
        cls.book_series = MarketTimeSeriesBuilder().build(cls.records, cls.contexts)

    # -- Task 1: time-series / leakage ------------------------------------
    def test_strict_ordering(self):
        for s in self.book_series.values():
            ts = [r.timestamp for r in s.ordered]
            self.assertEqual(ts, sorted(ts))

    def test_no_leakage_24h_bucket(self):
        # the 24h bucket may only use data available >= 24h before kickoff
        for key, s in self.book_series.items():
            pt = s.point(Horizon.H24)
            if pt and pt.record:
                self.assertGreaterEqual(pt.actual_age_hours, 24.0 - 1e-9, key)

    def test_opening_is_earliest(self):
        for s in self.book_series.values():
            if s.ordered:
                self.assertEqual(s.point(Horizon.OPENING).record, s.ordered[0])

    # -- Task 2: drift ----------------------------------------------------
    def test_drift_direction_signs(self):
        d = self.result.drift
        # Arsenal HOME was backed -> odds shortened -> negative total_drift
        self.assertLess(d["evt_ars_che|1X2|HOME@consensus"].total_drift, 0)
        self.assertEqual(d["evt_ars_che|1X2|HOME@consensus"].direction, "SHORTENING")
        # Liverpool HOME drifted out -> positive total_drift
        self.assertGreater(d["evt_liv_mci|1X2|HOME@consensus"].total_drift, 0)
        self.assertEqual(d["evt_liv_mci|1X2|HOME@consensus"].direction, "DRIFTING")

    def test_prob_and_odds_drift_opposite_sign(self):
        for v in self.result.drift.values():
            if v.total_drift and v.prob_drift_total and abs(v.total_drift) > 1e-3:
                self.assertNotEqual(
                    v.total_drift > 0, v.prob_drift_total > 0,
                    "odds-up must mean prob-down",
                )

    # -- Task 3: CLV ------------------------------------------------------
    def test_clv_raw_formula(self):
        c = self.result.clv_consensus["evt_liv_mci|1X2|HOME@consensus"]
        expected = (c.closing_odds - c.entry_odds) / c.entry_odds
        self.assertAlmostEqual(c.clv_raw, expected, places=9)
        # backer convention is the inverse move
        self.assertAlmostEqual(
            c.clv_backer, (c.entry_odds / c.closing_odds) - 1.0, places=9
        )

    def test_clv_pending_without_close(self):
        # a series with no pre-KO data and no close -> PENDING_CLOSE
        from datetime import datetime, timedelta, timezone
        from src.market.schema import MatchContext
        ko = datetime.now(timezone.utc) + timedelta(days=5)
        rec = OddsRecord("m_future", "pinnacle", "1X2", "HOME", 2.0,
                         ko + timedelta(hours=1))  # only an in-play stamp
        ctx = {"m_future": MatchContext("m_future", ko)}
        res = MeasurementPipeline().run([rec], ctx)
        key = "m_future|1X2|HOME@consensus"
        if key in res.clv_consensus:
            self.assertEqual(res.clv_consensus[key].status, "PENDING_CLOSE")

    def test_bookmaker_adjusted_weighting(self):
        # confidence-weighted CLV must sit within the per-book min/max range
        for v in self.result.clv_bookmaker_adjusted.values():
            vals = [x for x in v.per_bookmaker.values() if x is not None]
            if len(vals) >= 2 and v.confidence_weighted_clv is not None:
                self.assertGreaterEqual(v.confidence_weighted_clv, min(vals) - 1e-9)
                self.assertLessEqual(v.confidence_weighted_clv, max(vals) + 1e-9)

    # -- Task 4: efficiency ----------------------------------------------
    def test_consensus_score_bounded(self):
        for e in self.result.efficiency.values():
            if e.market_consensus_score is not None:
                self.assertGreaterEqual(e.market_consensus_score, 0.0)
                self.assertLessEqual(e.market_consensus_score, 1.0)

    def test_devig_sums_to_one(self):
        # de-vigged consensus probs of a market should sum ~1
        e = self.result.efficiency["evt_ars_che|1X2"]
        self.assertAlmostEqual(sum(e.consensus_prob.values()), 1.0, places=6)

    def test_overround_above_one(self):
        for e in self.result.efficiency.values():
            if e.mean_overround is not None:
                self.assertGreater(e.mean_overround, 1.0)

    def test_sharp_proxy_sign(self):
        # Arsenal HOME early-backed -> positive sharp proxy on HOME
        e = self.result.efficiency["evt_ars_che|1X2"]
        self.assertEqual(e.sharp_proxy_selection, "HOME")
        self.assertGreater(e.sharp_proxy_signal, 0)

    # -- Task 5: integrity ------------------------------------------------
    def test_integrity_catches_injected_defects(self):
        counts = self.result.integrity.counts
        self.assertGreaterEqual(counts.get("missing_snapshot_gaps", 0), 3)
        self.assertGreaterEqual(counts.get("duplicate_odds_sequences", 0), 3)
        self.assertGreaterEqual(counts.get("impossible_market_jumps", 0), 1)
        self.assertGreaterEqual(counts.get("timestamp_irregularities", 0), 1)

    def test_reproducible(self):
        r2 = MeasurementPipeline().run(*build_fixture())
        a = self.result.drift["evt_liv_mci|1X2|HOME@consensus"].total_drift
        b = r2.drift["evt_liv_mci|1X2|HOME@consensus"].total_drift
        self.assertAlmostEqual(a, b, places=12)


if __name__ == "__main__":
    unittest.main(verbosity=2)
