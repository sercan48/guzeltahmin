"""M11 — BetfairOutcomeProvider: dedicated settled-outcome provider — offline tests.

No network. All tests use FakeHttpClient or NullHttpClient.
"""

import json
import unittest

from src.market.activation import (
    FixtureMap, make_betfair_outcome_provider, parse_outcome_book,
    BetfairOutcomeProvider, Transport, FakeHttpClient, HttpResponse,
    RetryPolicy, StaticSecretProvider, AuthConfig, RequestAuditLog,
    NullHttpClient, ProviderError,
)

MID = "1.234"
APP_KEY = "OutcomeAppKey_XYZ"
SESSION = "OutcomeSession_ABC"

RUNNERS = {"47999": "HOME", "47998": "DRAW", "47997": "AWAY"}

BF_SETTLED_HOME = {"result": [{"marketId": MID, "status": "CLOSED",
    "totalMatched": 55000.0,
    "runners": [
        {"selectionId": 47999, "status": "WINNER"},
        {"selectionId": 47998, "status": "LOSER"},
        {"selectionId": 47997, "status": "LOSER"},
    ]}]}

BF_SETTLED_DRAW = {"result": [{"marketId": MID, "status": "CLOSED",
    "totalMatched": 30000.0,
    "runners": [
        {"selectionId": 47999, "status": "LOSER"},
        {"selectionId": 47998, "status": "WINNER"},
        {"selectionId": 47997, "status": "LOSER"},
    ]}]}

BF_SETTLED_AWAY = {"result": [{"marketId": MID, "status": "CLOSED",
    "totalMatched": 22000.0,
    "runners": [
        {"selectionId": 47999, "status": "LOSER"},
        {"selectionId": 47998, "status": "LOSER"},
        {"selectionId": 47997, "status": "WINNER"},
    ]}]}

BF_VOID = {"result": [{"marketId": MID, "status": "VOID", "runners": []}]}
BF_CANCELLED = {"result": [{"marketId": MID, "status": "CANCELLED", "runners": []}]}
BF_SUSPENDED = {"result": [{"marketId": MID, "status": "SUSPENDED", "runners": []}]}
BF_OPEN = {"result": [{"marketId": MID, "status": "OPEN",
    "totalMatched": 10000.0,
    "runners": [
        {"selectionId": 47999, "status": "ACTIVE"},
        {"selectionId": 47998, "status": "ACTIVE"},
    ]}]}
BF_UNMAPPED_WINNER = {"result": [{"marketId": MID, "status": "CLOSED",
    "totalMatched": 5000.0,
    "runners": [{"selectionId": 99999, "status": "WINNER"}]}]}


def lookup(market_id, selection_id):
    return RUNNERS.get(selection_id)


def _make_provider(script):
    sp = StaticSecretProvider({"BETFAIR_APP_KEY": APP_KEY, "BETFAIR_SESSION_TOKEN": SESSION})
    audit = RequestAuditLog()
    fm = FixtureMap()
    fm.register("epl_m1", "betfair_outcome", MID)
    transport = Transport(
        FakeHttpClient(script), secret_provider=sp,
        auth=AuthConfig(secret_key="BETFAIR_SESSION_TOKEN", header="X-Authentication"),
        retry=RetryPolicy(max_retries=0), sleeper=lambda s: None, audit=audit,
    )
    p = BetfairOutcomeProvider(fm, transport, sp)
    for sid, sel in RUNNERS.items():
        p.register_runner(MID, sid, sel)
    return p, transport, audit


class TestParserWinners(unittest.TestCase):
    def test_home_winner(self):
        o = parse_outcome_book(BF_SETTLED_HOME, lookup)
        self.assertEqual((o.status, o.home_goals, o.away_goals), ("COMPLETED", 1, 0))

    def test_draw_winner(self):
        o = parse_outcome_book(BF_SETTLED_DRAW, lookup)
        self.assertEqual((o.status, o.home_goals, o.away_goals), ("COMPLETED", 1, 1))

    def test_away_winner(self):
        o = parse_outcome_book(BF_SETTLED_AWAY, lookup)
        self.assertEqual((o.status, o.home_goals, o.away_goals), ("COMPLETED", 0, 1))

    def test_liquidity_captured(self):
        o = parse_outcome_book(BF_SETTLED_HOME, lookup)
        self.assertEqual(o.liquidity, 55000.0)

    def test_void_returns_cancelled(self):
        o = parse_outcome_book(BF_VOID, lookup)
        self.assertEqual(o.status, "CANCELLED")
        self.assertIsNone(o.home_goals)

    def test_cancelled_returns_cancelled(self):
        o = parse_outcome_book(BF_CANCELLED, lookup)
        self.assertEqual(o.status, "CANCELLED")

    def test_suspended_returns_cancelled(self):
        o = parse_outcome_book(BF_SUSPENDED, lookup)
        self.assertEqual(o.status, "CANCELLED")

    def test_no_winner_returns_none(self):
        self.assertIsNone(parse_outcome_book(BF_OPEN, lookup))

    def test_unmapped_runner_returns_none(self):
        self.assertIsNone(parse_outcome_book(BF_UNMAPPED_WINNER, lookup))

    def test_empty_raw_returns_none(self):
        self.assertIsNone(parse_outcome_book({}, lookup))


class TestEndpointAndAuth(unittest.TestCase):
    def test_endpoint_post_with_order_projection(self):
        sp = StaticSecretProvider({"BETFAIR_APP_KEY": APP_KEY})
        fm = FixtureMap()
        p = BetfairOutcomeProvider(fm, Transport(NullHttpClient()), sp)
        spec = p._endpoint(MID, "__outcome__")
        self.assertEqual(spec.method, "POST")
        self.assertIn("/json-rpc/v1", spec.url)
        self.assertEqual(spec.headers["X-Application"], APP_KEY)
        rpc_str = spec.params["_rpc"]
        self.assertIn("listMarketBook", rpc_str)
        self.assertIn("orderProjection", rpc_str)

    def test_fetch_outcome_dual_auth_secrets_not_in_audit(self):
        p, t, audit = _make_provider([HttpResponse(200, BF_SETTLED_HOME)])
        o = p.fetch_outcome("epl_m1")
        self.assertEqual(o.status, "COMPLETED")
        hdrs = t.http.last_request["headers"]
        self.assertEqual(hdrs["X-Application"], APP_KEY)
        self.assertEqual(hdrs["X-Authentication"], SESSION)
        dumped = json.dumps(audit.entries())
        self.assertNotIn(APP_KEY, dumped)
        self.assertNotIn(SESSION, dumped)

    def test_5xx_maps_to_provider_error(self):
        p, _, _ = _make_provider([HttpResponse(500, {})])
        with self.assertRaises(ProviderError):
            p.fetch_outcome("epl_m1")

    def test_network_error_maps_to_provider_error(self):
        p, _, _ = _make_provider([ConnectionError("timeout")])
        with self.assertRaises(ProviderError):
            p.fetch_outcome("epl_m1")

    def test_missing_fixture_raises(self):
        p, _, _ = _make_provider([HttpResponse(200, BF_SETTLED_HOME)])
        with self.assertRaises(ProviderError):
            p.fetch_outcome("unmapped_match")

    def test_fetch_snapshot_raises(self):
        p, _, _ = _make_provider([])
        with self.assertRaises(ProviderError):
            p.fetch_snapshot("epl_m1", "1X2", "CLOSE")

    def test_default_factory_no_network(self):
        sp = StaticSecretProvider({"BETFAIR_APP_KEY": APP_KEY, "BETFAIR_SESSION_TOKEN": SESSION})
        fm = FixtureMap()
        fm.register("epl_m1", "betfair_outcome", MID)
        p = make_betfair_outcome_provider(fm, secret_provider=sp)
        p.transport.retry = RetryPolicy(max_retries=0)
        p.transport.sleeper = lambda s: None
        for sid, sel in RUNNERS.items():
            p.register_runner(MID, sid, sel)
        with self.assertRaises(ProviderError):
            p.fetch_outcome("epl_m1")   # NullHttpClient -> no network

    def test_additivity_m11_hash_unchanged(self):
        import tests.test_m11_acceptance as m
        self.assertEqual(m.run_hash(m.baseline_providers()), m.TestM11Acceptance.BASELINE_HASH)


if __name__ == "__main__":
    unittest.main(verbosity=2)
