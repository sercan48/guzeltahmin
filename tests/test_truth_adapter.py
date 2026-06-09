"""M3 Truth Rule Enforcement adapter invariants. Pure, no network."""

import unittest
from datetime import datetime, timedelta, timezone

from src.market.schema import MatchContext, Horizon
from src.market.truth import (
    TruthStore, RawSnapshot, TruthAdapter, MeasurementMode, Provenance,
)

KO = datetime(2026, 3, 1, 18, 0, tzinfo=timezone.utc)

# vig-free 1X2 paths (implied probs sum to 1.0 at every horizon) so that
# de-vig is the identity and truth odds == provider odds exactly.
# (hours_before, snapshot_type, {sel: prob})
PATH = [
    (48.0, "OPEN",   {"HOME": 0.45, "DRAW": 0.30, "AWAY": 0.25}),
    (24.0, "T-24h",  {"HOME": 0.48, "DRAW": 0.29, "AWAY": 0.23}),
    (1.0,  "T-1h",   {"HOME": 0.52, "DRAW": 0.28, "AWAY": 0.20}),
    (0.0,  "CLOSE",  {"HOME": 0.54, "DRAW": 0.27, "AWAY": 0.19}),
]


def _odds(prob):
    return round(1.0 / prob, 6)


class TestTruthAdapter(unittest.TestCase):
    def setUp(self):
        self.store = TruthStore(":memory:")
        self.contexts = {"m": MatchContext("m", KO)}
        for hours, stype, probs in PATH:
            t = KO - timedelta(hours=hours)
            for sel, p in probs.items():
                self.store.ingest_snapshot(RawSnapshot("m", "pinnacle", "1X2", sel,
                                           _odds(p), stype, t))
        self.store.recompute_all()

    def tearDown(self):
        self.store.close()

    def _raw_records(self):
        from src.market.schema import OddsRecord
        recs = []
        for hours, stype, probs in PATH:
            t = KO - timedelta(hours=hours)
            for sel, p in probs.items():
                recs.append(OddsRecord("m", "pinnacle", "1X2", sel, _odds(p), t,
                                       snapshot_type=stype, source_id="raw"))
        return recs

    # 1. identical outputs when truth equals provider (vig-free => identity)
    def test_identical_when_truth_equals_provider(self):
        truth_adapter = TruthAdapter(self.store, MeasurementMode.TRUTH_ONLY)
        res_truth, _, _ = truth_adapter.run_measurement(self.contexts)
        legacy_adapter = TruthAdapter(self.store, MeasurementMode.LEGACY)
        res_legacy, _, _ = legacy_adapter.run_measurement(self.contexts, raw_records=self._raw_records())
        k = "m|1X2|HOME@consensus"
        self.assertAlmostEqual(res_truth.drift[k].total_drift,
                               res_legacy.drift[k].total_drift, places=6)
        self.assertAlmostEqual(res_truth.drift[k].prob_drift_total,
                               res_legacy.drift[k].prob_drift_total, places=6)

    # 2. confidence propagation
    def test_confidence_propagation(self):
        adapter = TruthAdapter(self.store)
        records, meta = adapter.build_inputs(self.contexts)
        # every emitted record carries the truth confidence in confidence_score
        for r in records:
            tr = self.store.get_truth("m", "1X2", r.selection, r.snapshot_type)
            self.assertAlmostEqual(r.confidence_score, tr.confidence, places=9)
        self.assertGreater(meta[("m", "1X2", "HOME")].confidence, 0.0)

    # 3. provenance propagation
    def test_provenance_propagation(self):
        adapter = TruthAdapter(self.store)
        records, meta = adapter.build_inputs(self.contexts)
        for r in records:
            self.assertTrue(r.source_id.startswith("truth:"))
            self.assertIn(Provenance.OBSERVED.value, r.source_id)
        self.assertEqual(meta[("m", "1X2", "HOME")].provenance, Provenance.OBSERVED.value)

    # 4. point-in-time correctness + 5. no leakage
    def test_point_in_time_no_leakage(self):
        adapter = TruthAdapter(self.store)
        cutoff = KO - timedelta(hours=24) + timedelta(minutes=1)  # after T-24h, before CLOSE
        records, meta = adapter.build_inputs(self.contexts, as_of=cutoff)
        stypes = {r.snapshot_type for r in records}
        self.assertIn("OPEN", stypes)
        self.assertIn("T-24h", stypes)
        self.assertNotIn("CLOSE", stypes)   # closing line must NOT leak pre-close
        self.assertNotIn("T-1h", stypes)
        # every emitted record's timestamp respects the cutoff
        for r in records:
            self.assertLessEqual(r.timestamp, cutoff)

    # only matches present in contexts (with kickoff) are emitted
    def test_only_contexted_matches_emitted(self):
        adapter = TruthAdapter(self.store)
        records, _ = adapter.build_inputs({})   # no contexts -> nothing
        self.assertEqual(records, [])

    # hybrid mode reports divergence without feeding raw downstream
    def test_hybrid_validation_zero_gap_when_identical(self):
        adapter = TruthAdapter(self.store, MeasurementMode.HYBRID)
        _, _, validation = adapter.run_measurement(self.contexts, raw_records=self._raw_records())
        self.assertIsNotNone(validation)
        # vig-free raw consensus == truth -> ~zero divergence
        self.assertLess(validation.max_abs_gap, 1e-6)

    def test_legacy_requires_raw(self):
        adapter = TruthAdapter(self.store, MeasurementMode.LEGACY)
        with self.assertRaises(ValueError):
            adapter.run_measurement(self.contexts)


if __name__ == "__main__":
    unittest.main(verbosity=2)
