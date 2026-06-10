"""M8.1 outcome ingestion & settlement ledger invariants. No network, no betting."""

import os
import tempfile
import unittest

from src.market.settlement import (
    OutcomeStatus, SettlementResult, MatchOutcome, resolve_market, SettlementLedger,
)


def outcome(match_id="m", status="COMPLETED", h=2, a=1, source="vendorA"):
    return MatchOutcome(match_id, status, h, a, source=source)


class TestResolver(unittest.TestCase):
    def test_1x2(self):
        o = outcome(h=2, a=1)
        self.assertEqual(resolve_market("1X2", "HOME", o), SettlementResult.WON)
        self.assertEqual(resolve_market("1X2", "DRAW", o), SettlementResult.LOST)
        self.assertEqual(resolve_market("1X2", "AWAY", o), SettlementResult.LOST)

    def test_1x2_draw(self):
        o = outcome(h=1, a=1)
        self.assertEqual(resolve_market("1X2", "DRAW", o), SettlementResult.WON)
        self.assertEqual(resolve_market("1X2", "HOME", o), SettlementResult.LOST)

    def test_over_under(self):
        o = outcome(h=2, a=1)   # total 3
        self.assertEqual(resolve_market("O/U", "OVER_2.5", o), SettlementResult.WON)
        self.assertEqual(resolve_market("O/U", "UNDER_2.5", o), SettlementResult.LOST)

    def test_ou_push_integer_line(self):
        o = outcome(h=1, a=1)   # total 2
        self.assertEqual(resolve_market("O/U", "OVER_2.0", o), SettlementResult.PUSH)

    def test_dnb_draw_push(self):
        o = outcome(h=1, a=1)
        self.assertEqual(resolve_market("DNB", "HOME", o), SettlementResult.PUSH)

    def test_void_status(self):
        for st in ("VOID", "CANCELLED", "ABANDONED", "POSTPONED"):
            o = outcome(status=st, h=None, a=None)
            self.assertEqual(resolve_market("1X2", "HOME", o), SettlementResult.VOID)

    def test_unknown_market_void(self):
        self.assertEqual(resolve_market("CORNERS", "X", outcome()), SettlementResult.VOID)


class TestOutcomeIngestion(unittest.TestCase):
    def setUp(self):
        self.led = SettlementLedger(":memory:")

    def tearDown(self):
        self.led.close()

    def test_ingest_and_get(self):
        _, dup = self.led.ingest_outcome(outcome())
        self.assertFalse(dup)
        got = self.led.get_outcome("m")
        self.assertEqual((got.home_goals, got.away_goals), (2, 1))

    def test_ingest_idempotent(self):
        self.led.ingest_outcome(outcome())
        _, dup = self.led.ingest_outcome(outcome())   # same match+source
        self.assertTrue(dup)


class TestSettlement(unittest.TestCase):
    def setUp(self):
        self.led = SettlementLedger(":memory:")
        self.led.ingest_outcome(outcome(h=2, a=1))    # HOME win, total 3

    def tearDown(self):
        self.led.close()

    def test_realized_roi_won(self):
        rec = self.led.settle("b1", "m", "1X2", "HOME", entry_odds=2.10, closing_odds=1.95)
        self.assertEqual(rec.result, "WON")
        self.assertAlmostEqual(rec.realized_roi, 1.10, places=9)

    def test_realized_roi_lost(self):
        rec = self.led.settle("b2", "m", "1X2", "AWAY", entry_odds=3.50)
        self.assertEqual(rec.result, "LOST")
        self.assertAlmostEqual(rec.realized_roi, -1.0, places=9)

    def test_realized_clv(self):
        rec = self.led.settle("b3", "m", "1X2", "HOME", entry_odds=2.10, closing_odds=1.95)
        self.assertAlmostEqual(rec.realized_clv, 2.10 / 1.95 - 1.0, places=9)

    def test_clv_none_without_close(self):
        rec = self.led.settle("b4", "m", "1X2", "HOME", entry_odds=2.10)
        self.assertIsNone(rec.realized_clv)

    def test_void_roi_zero(self):
        led = SettlementLedger(":memory:")
        led.ingest_outcome(outcome(status="CANCELLED", h=None, a=None))
        rec = led.settle("b", "m", "1X2", "HOME", entry_odds=2.0, closing_odds=1.9)
        self.assertEqual(rec.result, "VOID")
        self.assertTrue(rec.is_void)
        self.assertEqual(rec.realized_roi, 0.0)
        led.close()

    def test_pending_when_no_outcome(self):
        led = SettlementLedger(":memory:")
        self.assertIsNone(led.settle("b", "nomatch", "1X2", "HOME", 2.0))
        led.close()

    def test_duplicate_settlement_safe(self):
        a = self.led.settle("b1", "m", "1X2", "HOME", entry_odds=2.10, closing_odds=1.95)
        b = self.led.settle("b1", "m", "1X2", "HOME", entry_odds=2.10, closing_odds=1.95)
        self.assertEqual(a.entry_hash, b.entry_hash)
        self.assertEqual(self.led.replay().n_settlements, 1)   # only one ledger entry


class TestReplayAndChain(unittest.TestCase):
    def _populate(self, led):
        led.ingest_outcome(outcome("m1", h=2, a=0))
        led.ingest_outcome(outcome("m2", h=0, a=0))
        led.settle("b1", "m1", "1X2", "HOME", 2.0, 1.8)
        led.settle("b2", "m1", "1X2", "AWAY", 4.0, 4.2)
        led.settle("b3", "m2", "1X2", "DRAW", 3.3, 3.1)

    def test_replay_summary(self):
        led = SettlementLedger(":memory:")
        self._populate(led)
        s = led.replay()
        self.assertEqual(s.n_settlements, 3)
        self.assertTrue(s.chain_valid)
        # roi: HOME won (+1.0), AWAY lost (-1.0), DRAW won (+2.3) = +2.3
        self.assertAlmostEqual(s.total_roi, 2.3, places=6)
        led.close()

    def test_replay_deterministic(self):
        led = SettlementLedger(":memory:")
        self._populate(led)
        self.assertEqual(led.replay().to_dict(), led.replay().to_dict())
        led.close()

    def test_chain_verifies_and_detects_tamper(self):
        led = SettlementLedger(":memory:")
        self._populate(led)
        self.assertTrue(led.verify_chain())
        # tamper: flip a stored ROI directly
        led.conn.execute("UPDATE settlement_ledger SET realized_roi=99 WHERE seq=1")
        led.conn.commit()
        self.assertFalse(led.verify_chain())
        led.close()

    def test_replay_safe_reconstruction_across_reopen(self):
        path = os.path.join(tempfile.mkdtemp(), "settle.db")
        try:
            led = SettlementLedger(path)
            self._populate(led)
            s1 = led.replay().to_dict()
            led.close()
            led2 = SettlementLedger(path)            # fresh engine, same store
            s2 = led2.replay().to_dict()
            self.assertEqual(s1, s2)
            self.assertTrue(led2.verify_chain())
            led2.close()
        finally:
            if os.path.exists(path):
                os.remove(path)


if __name__ == "__main__":
    unittest.main(verbosity=2)
