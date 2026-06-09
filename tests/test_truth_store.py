"""Invariants for the M2 Truth Store (SQLite, OBSERVED-only). No network."""

import unittest
from datetime import datetime, timedelta, timezone

from src.market.truth import (
    TruthStore, RawSnapshot, ProviderClass, Provenance, classify_provider,
)

KO = datetime(2026, 1, 1, 18, 0, tzinfo=timezone.utc)


def snaps_for(match_id, snapshot_type, t, books):
    """books: {provider: {selection: odds}}"""
    out = []
    for provider, odds in books.items():
        for sel, o in odds.items():
            out.append(RawSnapshot(match_id, provider, "1X2", sel, o,
                                   snapshot_type, t))
    return out


class TestTruthStore(unittest.TestCase):
    def setUp(self):
        self.store = TruthStore(":memory:")

    def tearDown(self):
        self.store.close()

    def _ingest_basic(self):
        # sharp + soft books at CLOSE; sharp tighter
        t = KO - timedelta(minutes=2)
        books = {
            "pinnacle": {"HOME": 2.05, "DRAW": 3.50, "AWAY": 3.70},
            "bet365":   {"HOME": 2.02, "DRAW": 3.45, "AWAY": 3.65},
            "obscurebet": {"HOME": 2.20, "DRAW": 3.20, "AWAY": 3.40},
        }
        self.store.ingest_many(snaps_for("m1", "CLOSE", t, books))
        return books

    def test_classification(self):
        self.assertEqual(classify_provider("Pinnacle"), ProviderClass.SHARP.value)
        self.assertEqual(classify_provider("bet365"), ProviderClass.SEMI_SHARP.value)
        self.assertEqual(classify_provider("nobody"), ProviderClass.FREE.value)

    def test_recompute_produces_observed_truth_summing_to_one(self):
        self._ingest_basic()
        recs = self.store.recompute_truth("m1", "1X2", "CLOSE")
        self.assertEqual(len(recs), 3)
        self.assertAlmostEqual(sum(r.p_truth for r in recs), 1.0, places=9)
        for r in recs:
            self.assertEqual(r.provenance, Provenance.OBSERVED.value)
            self.assertGreater(r.confidence, 0.0)
            self.assertLessEqual(r.confidence, 1.0)

    def test_sharp_anchor_dominates_consensus(self):
        # truth HOME prob must sit nearer the sharp book's fair prob than the
        # soft outlier's (trust weighting pulls the consensus toward Pinnacle).
        from src.market.truth import devig
        self._ingest_basic()
        self.store.recompute_truth("m1", "1X2", "CLOSE")
        home = self.store.get_truth("m1", "1X2", "HOME", "CLOSE").p_truth
        sharp = devig({"HOME": 2.05, "DRAW": 3.50, "AWAY": 3.70}).fair_probs["HOME"]
        soft = devig({"HOME": 2.20, "DRAW": 3.20, "AWAY": 3.40}).fair_probs["HOME"]
        self.assertLess(abs(home - sharp), abs(home - soft))

    def test_point_in_time_read(self):
        # two snapshots at different times -> as_of read returns the right one
        early = KO - timedelta(hours=6)
        late = KO - timedelta(minutes=2)
        self.store.ingest_many(snaps_for("m2", "T6H", early,
                               {"pinnacle": {"HOME": 2.50, "DRAW": 3.30, "AWAY": 2.90}}))
        self.store.ingest_many(snaps_for("m2", "CLOSE", late,
                               {"pinnacle": {"HOME": 2.00, "DRAW": 3.50, "AWAY": 3.80}}))
        self.store.recompute_truth("m2", "1X2", "T6H")
        self.store.recompute_truth("m2", "1X2", "CLOSE")
        # as_of just after the early snapshot must NOT see the CLOSE row
        seen_early = self.store.get_truth("m2", "1X2", "HOME", as_of=early + timedelta(minutes=1))
        self.assertEqual(seen_early.snapshot_type, "T6H")
        # as_of after close sees the latest (CLOSE)
        seen_late = self.store.get_truth("m2", "1X2", "HOME", as_of=late + timedelta(minutes=1))
        self.assertEqual(seen_late.snapshot_type, "CLOSE")

    def test_get_truth_market_map_and_closing(self):
        self._ingest_basic()
        self.store.recompute_truth("m1", "1X2", "CLOSE")
        mkt = self.store.get_truth_market("m1", "1X2", "CLOSE")
        self.assertEqual(set(mkt), {"HOME", "DRAW", "AWAY"})
        self.assertAlmostEqual(sum(r.p_truth for r in mkt.values()), 1.0, places=9)
        self.assertIsNotNone(self.store.get_closing_truth("m1", "1X2", "HOME"))

    def test_partial_market_book_excluded(self):
        # a book missing a selection cannot be de-vigged; it must be skipped
        t = KO - timedelta(minutes=2)
        self.store.ingest_many(snaps_for("m3", "CLOSE", t,
                               {"pinnacle": {"HOME": 2.0, "DRAW": 3.5, "AWAY": 3.8}}))
        self.store.ingest_snapshot(RawSnapshot("m3", "softpartial", "1X2", "HOME", 2.1,
                                               "CLOSE", t))
        recs = self.store.recompute_truth("m3", "1X2", "CLOSE")
        self.assertEqual(len(recs), 3)
        for r in recs:
            self.assertIn("pinnacle", r.contributing_providers)
            self.assertNotIn("softpartial", r.contributing_providers)

    def test_latest_quote_per_provider_used(self):
        # later snapshot from same provider supersedes earlier in the same window
        t1 = KO - timedelta(minutes=10)
        t2 = KO - timedelta(minutes=2)
        self.store.ingest_many(snaps_for("m4", "CLOSE", t1,
                               {"pinnacle": {"HOME": 3.0, "DRAW": 3.3, "AWAY": 2.4}}))
        self.store.ingest_many(snaps_for("m4", "CLOSE", t2,
                               {"pinnacle": {"HOME": 2.0, "DRAW": 3.5, "AWAY": 3.8}}))
        recs = self.store.recompute_truth("m4", "1X2", "CLOSE")
        home = next(r for r in recs if r.selection == "HOME")
        # latest (t2) makes HOME the favourite (~0.49), not the earlier (~0.33)
        self.assertGreater(home.p_truth, 0.45)

    def test_confidence_higher_with_sharp_and_agreement(self):
        t = KO - timedelta(minutes=2)
        # tight sharp agreement
        self.store.ingest_many(snaps_for("a", "CLOSE", t,
            {"pinnacle": {"HOME": 2.00, "DRAW": 3.50, "AWAY": 3.80},
             "betfair":  {"HOME": 2.01, "DRAW": 3.49, "AWAY": 3.79}}))
        # soft only, disagreeing
        self.store.ingest_many(snaps_for("b", "CLOSE", t,
            {"obscurebet": {"HOME": 2.00, "DRAW": 3.50, "AWAY": 3.80},
             "the_odds_api": {"HOME": 2.60, "DRAW": 3.10, "AWAY": 2.70}}))
        ra = self.store.recompute_truth("a", "1X2", "CLOSE")[0]
        rb = self.store.recompute_truth("b", "1X2", "CLOSE")[0]
        self.assertGreater(ra.confidence, rb.confidence)

    def test_recompute_all(self):
        self._ingest_basic()
        n = self.store.recompute_all()
        self.assertEqual(n, 3)


if __name__ == "__main__":
    unittest.main(verbosity=2)
