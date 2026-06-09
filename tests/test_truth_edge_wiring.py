"""M3.2 Truth -> Edge wiring invariants. Pure, no network. Edge kernel unchanged."""

import unittest

from src.market.fixtures import build_fixture
from src.market.measurement_pipeline import MeasurementPipeline
from src.market.schema import Horizon
from src.market.edge import EdgeDetectionKernel
from src.market.edge.run_r1_3_edge import MODEL_PROBS, SEGMENT_META
from src.market.truth import TruthMeta, TruthEdgeAdjuster, Provenance

_TIER_RANK = {"REJECT": 0, "TIER_C": 1, "TIER_B": 2, "TIER_A": 3, "TIER_S": 4}


def _meta(confidence=0.9, provenance=Provenance.OBSERVED.value, sharp=0.9):
    return TruthMeta(confidence=confidence, provenance=provenance, as_of="2026-01-01T00:00:00+00:00",
                     truth_quality=confidence, truth_efficiency=1.0,
                     sharp_consensus_strength=sharp)


class TestTruthEdgeWiring(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        measurement = MeasurementPipeline().run(*build_fixture(), consensus_horizon=Horizon.H1)
        cls.edges = EdgeDetectionKernel().run(measurement, MODEL_PROBS, SEGMENT_META)
        cls.adj = TruthEdgeAdjuster()
        # Arsenal HOME is a positive-edge TIER_A pick
        cls.pos = cls.edges[("evt_ars_che", "1X2", "HOME")]
        assert cls.pos.metrics.sharp_adjusted_edge > 0

    # discount factors are all in (0, 1]
    def test_discount_factors_bounded(self):
        r = self.adj.adjust(self.pos, _meta())
        for d in (r.confidence_discount, r.provenance_discount,
                  r.sharp_consensus_discount, r.truth_discount):
            self.assertGreater(d, 0.0)
            self.assertLessEqual(d, 1.0 + 1e-12)

    # HARD GUARANTEE: positive edge is never increased
    def test_discount_only_positive_edge(self):
        for meta in (_meta(0.9), _meta(0.5), _meta(0.1),
                     _meta(provenance=Provenance.RECONSTRUCTED.value),
                     _meta(sharp=0.0), None):
            r = self.adj.adjust(self.pos, meta)
            self.assertLessEqual(r.edge_after_truth, r.edge_before_truth + 1e-12)
            self.assertGreaterEqual(r.edge_after_truth, 0.0)

    # confidence band: higher confidence -> smaller discount (less reduction)
    def test_confidence_monotonic(self):
        hi = self.adj.adjust(self.pos, _meta(confidence=0.95))
        lo = self.adj.adjust(self.pos, _meta(confidence=0.20))
        self.assertGreater(hi.confidence_discount, lo.confidence_discount)
        self.assertGreater(hi.edge_after_truth, lo.edge_after_truth)

    # provenance mapping: OBSERVED > RECONSTRUCTED trust
    def test_provenance_mapping(self):
        obs = self.adj.adjust(self.pos, _meta(provenance=Provenance.OBSERVED.value))
        rec = self.adj.adjust(self.pos, _meta(provenance=Provenance.RECONSTRUCTED.value))
        self.assertEqual(obs.provenance_discount, 1.0)
        self.assertLess(rec.provenance_discount, obs.provenance_discount)
        self.assertLess(rec.edge_after_truth, obs.edge_after_truth)

    # sharp consensus: stronger agreement -> smaller discount
    def test_sharp_consensus_monotonic(self):
        strong = self.adj.adjust(self.pos, _meta(sharp=0.95))
        weak = self.adj.adjust(self.pos, _meta(sharp=0.10))
        self.assertGreater(strong.sharp_consensus_discount, weak.sharp_consensus_discount)
        self.assertGreater(strong.edge_after_truth, weak.edge_after_truth)

    # tier never improves under truth adjustment
    def test_tier_never_improves(self):
        for meta in (_meta(0.9), _meta(0.3), _meta(provenance=Provenance.RECONSTRUCTED.value), None):
            r = self.adj.adjust(self.pos, meta)
            self.assertLessEqual(_TIER_RANK[r.tier_after], _TIER_RANK[r.tier_before])

    # aggressive discount can demote a borderline pick toward reject
    def test_low_trust_demotes(self):
        hi = self.adj.adjust(self.pos, _meta(0.95, Provenance.OBSERVED.value, 0.95))
        lo = self.adj.adjust(self.pos, _meta(0.05, Provenance.RECONSTRUCTED.value, 0.0))
        self.assertLessEqual(lo.eqs_after, hi.eqs_after)
        self.assertLessEqual(_TIER_RANK[lo.tier_after], _TIER_RANK[hi.tier_after])

    # monitoring outputs present
    def test_monitoring_fields(self):
        d = self.adj.adjust(self.pos, _meta()).to_dict()
        for f in ("edge_before_truth", "edge_after_truth", "truth_discount",
                  "confidence_discount", "provenance_discount", "sharp_consensus_discount",
                  "eqs_before", "eqs_after", "tier_before", "tier_after"):
            self.assertIn(f, d)

    # missing truth metadata -> conservative strong discount, still discount-only
    def test_missing_meta_conservative(self):
        r = self.adj.adjust(self.pos, None)
        self.assertEqual(r.provenance, "NONE")
        self.assertLess(r.truth_discount, 1.0)
        self.assertLessEqual(r.edge_after_truth, r.edge_before_truth)

    # batch
    def test_batch(self):
        metas = {k: _meta() for k in self.edges}
        out = self.adj.adjust_batch(self.edges, metas)
        self.assertEqual(set(out), set(self.edges))


if __name__ == "__main__":
    unittest.main(verbosity=2)
