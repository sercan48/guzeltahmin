"""R1.3 Edge Detection Kernel invariants (pure; no network, no ML training).

Validates the seven tasks: comparator math, the edge discount cascade, the
confidence blend, the agreement thresholds, EQS, tier rules, and the
evaluation-only historical validator.
"""

import unittest

from src.market.fixtures import build_fixture
from src.market.measurement_pipeline import MeasurementPipeline
from src.market.schema import Horizon
from src.market.edge import (
    EdgeDetectionKernel, SegmentMeta,
    EdgeMetricEngine, EdgeConfidenceEngine, MarketAgreementEngine,
    EdgeQualityScorer, SignalClassifier, AgreementClass, SignalTier,
    HistoricalValidator, SettledRecord,
)
from src.market.edge.run_r1_3_edge import MODEL_PROBS, SEGMENT_META


class TestEdgeKernel(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        measurement = MeasurementPipeline().run(*build_fixture(), consensus_horizon=Horizon.H1)
        cls.edges = EdgeDetectionKernel().run(measurement, MODEL_PROBS, SEGMENT_META)

    def home(self, mid):
        return self.edges[(mid, "1X2", "HOME")]

    # -- Task 1 -----------------------------------------------------------
    def test_gap_is_model_minus_market(self):
        e = self.home("evt_ars_che").comparator
        self.assertAlmostEqual(e.probability_gap, e.model_probability - e.market_probability, places=9)

    def test_percentile_in_unit_range(self):
        for e in self.edges.values():
            p = e.comparator.probability_gap_percentile
            if p is not None:
                self.assertGreaterEqual(p, 0.0)
                self.assertLessEqual(p, 1.0)

    # -- Task 2 -----------------------------------------------------------
    def test_raw_edge_formula(self):
        e = self.home("evt_juv_mil")
        c = e.comparator
        self.assertAlmostEqual(e.metrics.raw_edge, c.model_probability / c.market_probability - 1, places=9)

    def test_cascade_is_monotone_discount_for_positive_edge(self):
        # for a positive raw edge, each stage is <= previous in magnitude
        e = self.home("evt_ars_che").metrics
        chain = [e.raw_edge, e.calibrated_edge, e.market_adjusted_edge,
                 e.drift_adjusted_edge, e.sharp_adjusted_edge]
        for a, b in zip(chain, chain[1:]):
            self.assertLessEqual(abs(b), abs(a) + 1e-12)

    def test_factors_bounded_0_1(self):
        for e in self.edges.values():
            for f in e.metrics.factors.values():
                self.assertGreaterEqual(f, 0.0)
                self.assertLessEqual(f, 1.0)

    def test_calibrated_edge_shrinks_by_calibration(self):
        m = EdgeMetricEngine().compute(0.6, 0.5, calibration_quality=0.5,
                                       mean_overround=1.0, prob_drift_total=0.0,
                                       prob_velocity=0.0, sharp_proxy=0.0)
        self.assertAlmostEqual(m.calibrated_edge, 0.5 * m.raw_edge, places=9)

    def test_sharp_contradiction_discounts(self):
        # model likes HOME (raw>0) but sharp money strongly against -> f_sharp < 1
        m = EdgeMetricEngine().compute(0.6, 0.5, calibration_quality=1.0,
                                       mean_overround=1.0, prob_drift_total=0.0,
                                       prob_velocity=0.0, sharp_proxy=-0.05)
        self.assertLess(m.factors["f_sharp"], 1.0)

    # -- Task 3 -----------------------------------------------------------
    def test_confidence_bounded(self):
        for e in self.edges.values():
            self.assertGreaterEqual(e.confidence.edge_confidence_score, 0.0)
            self.assertLessEqual(e.confidence.edge_confidence_score, 1.0)

    # -- Task 4 -----------------------------------------------------------
    def test_agreement_classes(self):
        self.assertEqual(self.home("evt_ars_che").agreement.agreement_class, AgreementClass.MODEL_STRONGER.value)
        self.assertEqual(self.home("evt_liv_mci").agreement.agreement_class, AgreementClass.CONFLICT.value)
        self.assertEqual(self.home("evt_bar_rma").agreement.agreement_class, AgreementClass.AGREE.value)

    def test_conflict_on_implausible_z(self):
        a = MarketAgreementEngine().classify(gap=0.2, zscore=3.0, prob_drift_total=0.1)
        self.assertEqual(a.agreement_class, AgreementClass.CONFLICT.value)

    # -- Task 6 -----------------------------------------------------------
    def test_eqs_gated_on_nonpositive_edge(self):
        q = EdgeQualityScorer().compute(sharp_adjusted_edge=-0.01, edge_confidence_score=0.9, clv_alignment=0.9)
        self.assertEqual(q.eqs, 0.0)

    def test_eqs_range(self):
        for e in self.edges.values():
            self.assertGreaterEqual(e.eqs.eqs, 0.0)
            self.assertLessEqual(e.eqs.eqs, 100.0)

    # -- Task 7 -----------------------------------------------------------
    def test_conflict_rejected(self):
        self.assertEqual(self.home("evt_liv_mci").classification.tier, SignalTier.REJECT.value)

    def test_tier_spread(self):
        tiers = {mid: self.home(mid).classification.tier for mid in
                 ("evt_ars_che", "evt_juv_mil", "evt_bay_dor")}
        self.assertEqual(tiers["evt_ars_che"], SignalTier.A.value)
        self.assertEqual(tiers["evt_juv_mil"], SignalTier.B.value)
        self.assertEqual(tiers["evt_bay_dor"], SignalTier.C.value)

    def test_tier_s_reachable_when_confirmed_and_settled(self):
        m = EdgeMetricEngine().compute(0.58, 0.50, calibration_quality=0.90,
                                       mean_overround=1.03, prob_drift_total=0.05,
                                       prob_velocity=0.002, sharp_proxy=0.04)
        cf = EdgeConfidenceEngine().compute(calibration_quality=0.90, market_consensus_score=0.99,
                                            gap_zscore=1.6, f_drift=m.factors["f_drift"], clv_alignment=0.80)
        q = EdgeQualityScorer().compute(sharp_adjusted_edge=m.sharp_adjusted_edge,
                                        edge_confidence_score=cf.edge_confidence_score, clv_alignment=0.80)
        cl = SignalClassifier().classify(eqs=q.eqs, edge_confidence_score=cf.edge_confidence_score,
                                         agreement_class=AgreementClass.MODEL_STRONGER.value,
                                         sharp_adjusted_edge=m.sharp_adjusted_edge,
                                         f_drift=m.factors["f_drift"], f_sharp=m.factors["f_sharp"])
        self.assertEqual(cl.tier, SignalTier.S.value)

    # -- Task 5 -----------------------------------------------------------
    def test_validation_metrics_and_clv_rank(self):
        recs = []
        spec = {"TIER_S": (20, 0.62, 2.10, 1.95), "REJECT": (20, 0.40, 2.90, 3.10)}
        for tier, (n, wr, entry, close) in spec.items():
            wins = round(n * wr)
            for i in range(n):
                recs.append(SettledRecord(f"{tier}_{i}", "HOME", wr, 1.0 / entry,
                                          entry, close, 1 if i < wins else 0, tier=tier))
        res = HistoricalValidator().evaluate(recs, by="tier")
        self.assertIn("ALL", res)
        # CLV separates a good tier (beat the close) from a rejected one
        self.assertGreater(res["TIER_S"].clv, res["REJECT"].clv)
        self.assertEqual(res["TIER_S"].pct_beat_close, 1.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
