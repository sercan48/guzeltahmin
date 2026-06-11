"""PHASE-LIVE L2 Betfair production adapter — offline fixture tests. No network."""

import json
import unittest
from datetime import datetime, timedelta, timezone

from src.market.activation import (
    FixtureMap, BetfairLiveProvider, make_betfair_provider, parse_betfair_book,
    parse_betfair_settled, Transport, FakeHttpClient, HttpResponse, RetryPolicy,
    StaticSecretProvider, AuthConfig, RequestAuditLog, NullHttpClient, ProviderError,
    IngestionBridge,
)
from src.market.scheduler import SnapshotScheduler, ManualClock
from src.market.truth import TruthStore

MID = "1.234"
APP_KEY = "AppKeySecret_XYZ"
SESSION = "SessionTokenSecret_ABC"
TS = "2026-05-09T14:58:00Z"

BF_BOOK = {"result": [{
    "marketId": "1.234", "publishTime": TS, "status": "OPEN", "runners": [
        {"selectionId": 47999, "lastPriceTraded": 2.02, "totalMatched": 18000.0, "status": "ACTIVE"},
        {"selectionId": 47998, "lastPriceTraded": 3.38, "totalMatched": 9000.0, "status": "ACTIVE"},
        {"selectionId": 47997, "lastPriceTraded": 3.62, "totalMatched": 7000.0, "status": "ACTIVE"}]}]}
BF_SETTLED = {"result": [{"marketId": "1.234", "status": "SETTLED", "runners": [
    {"selectionId": 47999, "status": "WINNER"},
    {"selectionId": 47998, "status": "LOSER"},
    {"selectionId": 47997, "status": "LOSER"}]}]}

RUNNERS = {"47999": "HOME", "47998": "DRAW", "47997": "AWAY"}


def fm():
    m = FixtureMap()
    m.register("epl_m1", "betfair", MID)
    return m


def lookup(market_id, selection_id):
    return RUNNERS.get(selection_id)


def static_provider(script):
    audit = RequestAuditLog()
    sp = StaticSecretProvider({"BETFAIR_APP_KEY": APP_KEY, "BETFAIR_SESSION_TOKEN": SESSION})
    t = Transport(FakeHttpClient(script), secret_provider=sp,
                  auth=AuthConfig(secret_key="BETFAIR_SESSION_TOKEN", header="X-Authentication"),
                  retry=RetryPolicy(max_retries=1), sleeper=lambda s: None, audit=audit)
    p = BetfairLiveProvider(fm(), t, sp)
    for sid, sel in RUNNERS.items():
        p.register_runner(MID, sid, sel)
    return p, t, audit


class TestParsers(unittest.TestCase):
    def test_parse_book_with_liquidity(self):
        qs = parse_betfair_book(BF_BOOK, "1X2", lookup)
        home = next(q for q in qs if q.selection == "HOME")
        self.assertEqual(home.odds, 2.02)
        self.assertEqual(home.liquidity, 18000.0)
        self.assertEqual(home.timestamp, TS)
        self.assertEqual(home.provider, "betfair")

    def test_parse_book_skips_unmapped(self):
        qs = parse_betfair_book(BF_BOOK, "1X2", lambda m, s: "HOME" if s == "47999" else None)
        self.assertEqual({q.selection for q in qs}, {"HOME"})

    def test_parse_empty_raises(self):
        with self.assertRaises(ProviderError):
            parse_betfair_book({"result": []}, "1X2", lookup)

    def test_parse_settled_winner(self):
        o = parse_betfair_settled(BF_SETTLED, lookup)
        self.assertEqual((o.status, o.home_goals, o.away_goals), ("COMPLETED", 1, 0))

    def test_parse_settled_void(self):
        o = parse_betfair_settled({"result": [{"marketId": MID, "status": "VOID", "runners": []}]}, lookup)
        self.assertEqual(o.status, "CANCELLED")


class TestEndpointAndTransport(unittest.TestCase):
    def test_endpoint_post_with_app_key(self):
        sp = StaticSecretProvider({"BETFAIR_APP_KEY": APP_KEY})
        p = BetfairLiveProvider(fm(), Transport(NullHttpClient()), sp)
        spec = p._endpoint(MID, "1X2")
        self.assertEqual(spec.method, "POST")
        self.assertIn("/json-rpc/v1", spec.url)
        self.assertEqual(spec.headers["X-Application"], APP_KEY)
        self.assertIn("listMarketBook", spec.params["_rpc"])

    def test_fetch_snapshot_dual_auth_no_secret_leak(self):
        p, t, audit = static_provider([HttpResponse(200, BF_BOOK)])
        qs = p.fetch_snapshot("epl_m1", "1X2", "CLOSE")
        self.assertEqual({q.selection for q in qs}, {"HOME", "DRAW", "AWAY"})
        hdrs = t.http.last_request["headers"]
        self.assertEqual(hdrs["X-Application"], APP_KEY)            # app key injected
        self.assertEqual(hdrs["X-Authentication"], SESSION)        # session token injected
        dumped = json.dumps(audit.entries())
        self.assertNotIn(APP_KEY, dumped)                          # neither secret logged
        self.assertNotIn(SESSION, dumped)

    def test_fetch_outcome(self):
        p, _, _ = static_provider([HttpResponse(200, BF_SETTLED)])
        o = p.fetch_outcome("epl_m1")
        self.assertEqual((o.home_goals, o.away_goals), (1, 0))


class TestErrorMapping(unittest.TestCase):
    def test_5xx_maps_to_provider_error(self):
        p, _, _ = static_provider([HttpResponse(500, {})])
        with self.assertRaises(ProviderError):
            p.fetch_snapshot("epl_m1", "1X2", "CLOSE")

    def test_network_error_maps(self):
        p, _, _ = static_provider([ConnectionError("dns")])
        with self.assertRaises(ProviderError):
            p.fetch_snapshot("epl_m1", "1X2", "CLOSE")

    def test_missing_fixture_raises(self):
        p, _, _ = static_provider([HttpResponse(200, BF_BOOK)])
        with self.assertRaises(ProviderError):
            p.fetch_snapshot("unmapped", "1X2", "CLOSE")


class TestConfigGated(unittest.TestCase):
    def test_default_factory_no_network(self):
        sp = StaticSecretProvider({"BETFAIR_APP_KEY": APP_KEY, "BETFAIR_SESSION_TOKEN": SESSION})
        p = make_betfair_provider(fm(), secret_provider=sp)
        p.transport.retry = RetryPolicy(max_retries=0)
        p.transport.sleeper = lambda s: None
        for sid, sel in RUNNERS.items():
            p.register_runner(MID, sid, sel)
        with self.assertRaises(ProviderError):
            p.fetch_snapshot("epl_m1", "1X2", "CLOSE")          # NullHttpClient -> no network


class TestBridgeIntegration(unittest.TestCase):
    def test_plugs_in_and_captures_liquidity(self):
        KO = datetime(2026, 5, 9, 15, 0, tzinfo=timezone.utc)
        clock = ManualClock(KO - timedelta(hours=100))
        sched = SnapshotScheduler(clock, ":memory:")
        sched.schedule_match("epl_m1", KO)
        truth = TruthStore(":memory:")
        p, _, _ = static_provider([HttpResponse(200, BF_BOOK)])
        bridge = IngestionBridge(sched, truth, [p], db_path=":memory:")
        clock.set(KO)
        results = bridge.process_due()
        self.assertEqual(len(results), 7)
        self.assertTrue(all(r.status == "SUCCESS" for r in results))
        # Betfair liquidity flowed through ProviderQuote -> bridge capture
        self.assertEqual(bridge.liquidity_for("epl_m1", "1X2", "HOME", "CLOSE").get("betfair"),
                         18000.0)
        bridge.close(); truth.close(); sched.close()


class TestAdditivity(unittest.TestCase):
    def test_m11_acceptance_hash_unchanged(self):
        import tests.test_m11_acceptance as m
        self.assertEqual(m.run_hash(m.baseline_providers()),
                         m.TestM11Acceptance.BASELINE_HASH)


if __name__ == "__main__":
    unittest.main(verbosity=2)
