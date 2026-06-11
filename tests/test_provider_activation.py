"""Zero-redesign provider activation proof.

Proves a licensed PinnacleProvider + BetfairProvider plug into the UNCHANGED
ingestion bridge and feed the UNCHANGED downstream chain (truth -> measurement
-> edge). No network, deterministic.
"""

import unittest
from datetime import datetime, timezone

from src.market.schema import MatchContext, Horizon
from src.market.truth import TruthStore, TruthAdapter, MeasurementMode
from src.market.edge import EdgeDetectionKernel
from src.market.scheduler import SnapshotScheduler, ManualClock
from src.market.activation import (
    FixtureMap, PinnacleProvider, BetfairProvider, IngestionBridge,
    ProviderQuote, ProviderError,
)

KO = datetime(2026, 3, 1, 18, 0, tzinfo=timezone.utc)
CLOSE_TS = "2026-03-01T17:58:00+00:00"   # provider's real quote time near kickoff


def pinnacle_transport(fid, market):
    if market == "__outcome__":
        return {"status": "COMPLETED", "home": 2, "away": 1}
    return {"lastUpdate": CLOSE_TS,
            "markets": {"1X2": {"HOME": 2.00, "DRAW": 3.40, "AWAY": 3.60}}}


def betfair_transport(fid, market):
    if market == "__outcome__":
        return {"status": "COMPLETED", "home": 2, "away": 1}
    return {"lastMatchTime": CLOSE_TS,
            "runners": [
                {"selection": "HOME", "lastPriceTraded": 2.02, "totalMatched": 18000.0},
                {"selection": "DRAW", "lastPriceTraded": 3.38, "totalMatched": 9000.0},
                {"selection": "AWAY", "lastPriceTraded": 3.62, "totalMatched": 7000.0},
            ]}


class TestProviderQuoteExtension(unittest.TestCase):
    def test_quote_carries_timestamp_and_liquidity(self):
        q = ProviderQuote("betfair", "1X2", "HOME", 2.02, "SHARP",
                          timestamp=CLOSE_TS, liquidity=18000.0)
        self.assertEqual(q.timestamp, CLOSE_TS)
        self.assertEqual(q.liquidity, 18000.0)

    def test_backward_compatible_defaults(self):
        q = ProviderQuote("pinnacle", "1X2", "HOME", 2.0)   # old call site
        self.assertIsNone(q.timestamp)
        self.assertIsNone(q.liquidity)


class TestFixtureMap(unittest.TestCase):
    def test_bidirectional(self):
        fm = FixtureMap()
        fm.register("m", "pinnacle", "P-1001")
        self.assertEqual(fm.to_provider("m", "pinnacle"), "P-1001")
        self.assertEqual(fm.to_match("pinnacle", "P-1001"), "m")
        self.assertIsNone(fm.to_provider("m", "betfair"))


class TestAdapters(unittest.TestCase):
    def setUp(self):
        self.fm = FixtureMap()
        self.fm.register("m", "pinnacle", "P-1001")
        self.fm.register("m", "betfair", "1.234")

    def test_pinnacle_parses_with_timestamp(self):
        p = PinnacleProvider(self.fm, transport=pinnacle_transport)
        qs = p.fetch_snapshot("m", "1X2", "CLOSE")
        self.assertEqual({q.selection for q in qs}, {"HOME", "DRAW", "AWAY"})
        self.assertTrue(all(q.timestamp == CLOSE_TS for q in qs))
        self.assertTrue(all(q.provider_class == "SHARP" for q in qs))

    def test_betfair_parses_with_liquidity(self):
        b = BetfairProvider(self.fm, transport=betfair_transport)
        qs = b.fetch_snapshot("m", "1X2", "CLOSE")
        home = next(q for q in qs if q.selection == "HOME")
        self.assertEqual(home.liquidity, 18000.0)
        self.assertEqual(home.timestamp, CLOSE_TS)

    def test_network_isolated_by_default(self):
        p = PinnacleProvider(self.fm)            # no transport -> no network
        with self.assertRaises(ProviderError):
            p.fetch_snapshot("m", "1X2", "CLOSE")

    def test_missing_fixture_mapping_raises(self):
        p = PinnacleProvider(self.fm, transport=pinnacle_transport)
        with self.assertRaises(ProviderError):
            p.fetch_snapshot("unknown_match", "1X2", "CLOSE")


class TestZeroRedesignEndToEnd(unittest.TestCase):
    """The proof: real adapters -> UNCHANGED bridge -> UNCHANGED downstream."""

    def setUp(self):
        self.fm = FixtureMap()
        self.fm.register("m", "pinnacle", "P-1001")
        self.fm.register("m", "betfair", "1.234")
        self.clock = ManualClock(KO)
        self.sched = SnapshotScheduler(self.clock, ":memory:")
        self.truth = TruthStore(":memory:")
        providers = [
            PinnacleProvider(self.fm, transport=pinnacle_transport),
            BetfairProvider(self.fm, transport=betfair_transport),
        ]
        # IngestionBridge constructor is UNCHANGED — real adapters drop in.
        self.bridge = IngestionBridge(self.sched, self.truth, providers, db_path=":memory:")
        self.sched.schedule_match("m", KO)
        self.clock.set(KO)
        self.bridge.process_due()

    def tearDown(self):
        self.bridge.close(); self.truth.close(); self.sched.close()

    def test_both_providers_ingested_into_truth(self):
        close = self.truth.get_closing_truth("m", "1X2", "HOME")
        self.assertIn("pinnacle", close.contributing_providers)
        self.assertIn("betfair", close.contributing_providers)
        self.assertEqual(close.provenance, "OBSERVED")

    def test_provider_timestamp_threaded_to_truth(self):
        close = self.truth.get_closing_truth("m", "1X2", "HOME")
        # truth as_of is the provider's REAL quote time, not the scheduled time
        self.assertEqual(close.as_of, CLOSE_TS)

    def test_liquidity_captured(self):
        liq = self.bridge.liquidity_for("m", "1X2", "HOME", "CLOSE")
        self.assertEqual(liq.get("betfair"), 18000.0)
        self.assertNotIn("pinnacle", liq)        # Pinnacle has no liquidity
        self.assertEqual(self.bridge.monitor()["provider_coverage"], ["betfair", "pinnacle"])

    def test_downstream_runs_unmodified(self):
        # the SAME truth adapter / measurement / edge modules consume the truth
        contexts = {"m": MatchContext("m", KO)}
        adapter = TruthAdapter(self.truth, MeasurementMode.TRUTH_ONLY)
        measurement, meta, _ = adapter.run_measurement(contexts, consensus_horizon=Horizon.H1)
        self.assertIn("m|1X2", measurement.efficiency)
        model_probs = {("m", "1X2", "HOME"): 0.60, ("m", "1X2", "DRAW"): 0.24,
                       ("m", "1X2", "AWAY"): 0.16}
        edges = EdgeDetectionKernel().run(measurement, model_probs)
        self.assertIn(("m", "1X2", "HOME"), edges)
        # the edge kernel produced a real result off provider-sourced truth
        self.assertIsNotNone(edges[("m", "1X2", "HOME")].classification.tier)


if __name__ == "__main__":
    unittest.main(verbosity=2)
