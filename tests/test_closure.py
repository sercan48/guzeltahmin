"""M8.2 closing-truth lock & immutable closure invariants. No network, no betting."""

import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

from src.market.truth import TruthStore, RawSnapshot
from src.market.settlement import ClosureLedger, CloseKind

KO = datetime(2026, 2, 1, 18, 0, tzinfo=timezone.utc)


def seed_store(store, match_id="m", close=True):
    """Seed OPEN + (optionally) CLOSE truth for a 1X2 market."""
    def ingest(stype, hours, probs):
        t = KO - timedelta(hours=hours)
        for sel, p in probs.items():
            store.ingest_snapshot(RawSnapshot(match_id, "pinnacle", "1X2", sel,
                                              round(1.0 / p, 4), stype, t))
        store.recompute_truth(match_id, "1X2", stype)
    ingest("OPEN", 48, {"HOME": 0.45, "DRAW": 0.30, "AWAY": 0.25})
    if close:
        ingest("CLOSE", 0.05, {"HOME": 0.52, "DRAW": 0.27, "AWAY": 0.21})


class TestClosingLock(unittest.TestCase):
    def setUp(self):
        self.store = TruthStore(":memory:")
        self.led = ClosureLedger(":memory:")

    def tearDown(self):
        self.store.close(); self.led.close()

    def test_kickoff_lock_captures_close(self):
        seed_store(self.store)
        recs = self.led.lock(self.store, "m", "1X2", KO)
        self.assertEqual({r.selection for r in recs}, {"HOME", "DRAW", "AWAY"})
        self.assertTrue(self.led.is_locked("m", "1X2"))
        home = self.led.get_close("m", "1X2", "HOME")
        self.assertEqual(home.close_kind, CloseKind.OBSERVED_CLOSE.value)
        self.assertGreater(home.o_close, 1.0)
        self.assertIsNotNone(home.confidence)
        self.assertIn("pinnacle", home.source_composition)

    def test_duplicate_lock_idempotent(self):
        seed_store(self.store)
        first = self.led.lock(self.store, "m", "1X2", KO)
        second = self.led.lock(self.store, "m", "1X2", KO)   # re-lock
        self.assertEqual([r.entry_hash for r in first], [r.entry_hash for r in second])
        self.assertEqual(self.led.replay().n_records, len(first))   # no new rows

    def test_late_update_cannot_alter_close(self):
        seed_store(self.store)
        locked = self.led.lock(self.store, "m", "1X2", KO)
        home_before = self.led.get_close("m", "1X2", "HOME").o_close
        # a LATE provider update arrives AFTER kickoff and truth recomputes
        late = KO + timedelta(minutes=30)
        for sel, p in {"HOME": 0.80, "DRAW": 0.12, "AWAY": 0.08}.items():
            self.store.ingest_snapshot(RawSnapshot("m", "pinnacle", "1X2", sel,
                                                   round(1.0 / p, 4), "LIVE", late))
        self.store.recompute_truth("m", "1X2", "LIVE")
        # re-lock is idempotent AND the locked close is unchanged
        self.led.lock(self.store, "m", "1X2", KO)
        self.assertEqual(self.led.get_close("m", "1X2", "HOME").o_close, home_before)

    def test_point_in_time_excludes_post_kickoff_truth(self):
        # if we lock a match whose ONLY truth is post-kickoff, close is MISSING
        late = KO + timedelta(minutes=10)
        for sel, p in {"HOME": 0.5, "DRAW": 0.3, "AWAY": 0.2}.items():
            self.store.ingest_snapshot(RawSnapshot("z", "pinnacle", "1X2", sel,
                                                   round(1.0 / p, 4), "LIVE", late))
        self.store.recompute_truth("z", "1X2", "LIVE")
        recs = self.led.lock(self.store, "z", "1X2", KO)
        self.assertEqual(recs[0].close_kind, CloseKind.MISSING.value)

    def test_fallback_when_no_close_snapshot(self):
        seed_store(self.store, close=False)   # only OPEN exists
        recs = self.led.lock(self.store, "m", "1X2", KO)
        self.assertTrue(all(r.close_kind == CloseKind.FALLBACK.value for r in recs))

    def test_stale_close_flagged(self):
        seed_store(self.store, close=False)   # close = OPEN @ 48h before KO -> stale
        recs = self.led.lock(self.store, "m", "1X2", KO)
        self.assertTrue(all(r.is_stale for r in recs))

    def test_missing_close_handling(self):
        recs = self.led.lock(self.store, "ghost", "1X2", KO)   # no truth at all
        self.assertEqual(len(recs), 1)
        self.assertEqual(recs[0].close_kind, CloseKind.MISSING.value)
        self.assertIsNone(recs[0].o_close)


class TestDeterminismAndChain(unittest.TestCase):
    def _run(self, led):
        store = TruthStore(":memory:")
        seed_store(store)
        led.lock(store, "m", "1X2", KO)
        store.close()

    def test_immutable_close_independent_of_store_recompute(self):
        store = TruthStore(":memory:")
        seed_store(store)
        led = ClosureLedger(":memory:")
        led.lock(store, "m", "1X2", KO)
        before = led.get_close("m", "1X2", "HOME").o_close
        # store recomputes a new truth version; locked close must not move
        store.recompute_truth("m", "1X2", "CLOSE")
        self.assertEqual(led.get_close("m", "1X2", "HOME").o_close, before)
        store.close(); led.close()

    def test_chain_valid_and_tamper_detected(self):
        led = ClosureLedger(":memory:")
        self._run(led)
        self.assertTrue(led.verify_chain())
        led.conn.execute("UPDATE closure_ledger SET o_close=9.99 WHERE seq=1")
        led.conn.commit()
        self.assertFalse(led.verify_chain())
        led.close()

    def test_replay_deterministic_across_reopen(self):
        path = os.path.join(tempfile.mkdtemp(), "closure.db")
        try:
            led = ClosureLedger(path)
            self._run(led)
            s1 = led.replay().to_dict()
            led.close()
            led2 = ClosureLedger(path)
            s2 = led2.replay().to_dict()
            self.assertEqual(s1, s2)
            self.assertTrue(led2.verify_chain())
            led2.close()
        finally:
            if os.path.exists(path):
                os.remove(path)


if __name__ == "__main__":
    unittest.main(verbosity=2)
