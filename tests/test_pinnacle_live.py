"""PHASE-LIVE L1 Pinnacle production adapter — offline fixture tests. No network."""

import json
import unittest
from datetime import datetime, timedelta, timezone

from src.market.activation import (
    FixtureMap, PinnacleLiveProvider, make_pinnacle_provider, parse_pinnacle_snapshot,
    parse_pinnacle_settled, Transport, FakeHttpClient, HttpResponse, RetryPolicy,
    StaticSecretProvider, AuthConfig, RequestAuditLog, NullHttpClient, ProviderError,
    IngestionBridge,
)
from src.market.scheduler import SnapshotScheduler, ManualClock
from src.market.truth import TruthStore

FID = "1568987154"
SECRET = "cGlubmFjbGU6c2VjcmV0"   # base64-ish token (fake)
TS = "2026-05-09T14:58:00Z"

PINN_ODDS = {"sportId": 29, "last": 487744001, "leagues": [
    {"id": 1980, "events": [
        {"id": 1568987154, "periods": [
            {"number": 0, "cutoff": TS,
             "moneyline": {"home": 2.05, "draw": 3.40, "away": 3.60},
             "totals": [{"points": 2.5, "over": 1.95, "under": 1.90}]}]}]}]}
PINN_SETTLED = {"sportId": 29, "leagues": [{"id": 1980, "events": [
    {"id": 1568987154, "status": "COMPLETED",
     "periods": [{"number": 0, "score": {"home": 2, "away": 1}}]}]}]}


def fm():
    m = FixtureMap()
    m.register("epl_m1", "pinnacle", FID)
    return m


def static_provider(script):
    """PinnacleLiveProvider on a fake transport (no real sleep/network)."""
    audit = RequestAuditLog()
    t = Transport(FakeHttpClient(script), secret_provider=StaticSecretProvider({"PINNACLE_API_KEY": SECRET}),
                  auth=AuthConfig(secret_key="PINNACLE_API_KEY", header="Authorization", prefix="Basic "),
                  retry=RetryPolicy(max_retries=1), sleeper=lambda s: None, audit=audit)
    p = PinnacleLiveProvider(fm(), t, base_url="https://api.pinnacle.com")
    return p, t, audit


class TestParsers(unittest.TestCase):
    def test_parse_1x2(self):
        qs = parse_pinnacle_snapshot(PINN_ODDS, "1X2", FID)
        self.assertEqual({q.selection: q.odds for q in qs},
                         {"HOME": 2.05, "DRAW": 3.40, "AWAY": 3.60})
        self.assertTrue(all(q.provider == "pinnacle" and q.provider_class == "SHARP" for q in qs))
        self.assertTrue(all(q.timestamp == TS for q in qs))

    def test_parse_totals(self):
        qs = parse_pinnacle_snapshot(PINN_ODDS, "O/U", FID)
        self.assertEqual({q.selection for q in qs}, {"OVER_2.5", "UNDER_2.5"})

    def test_parse_missing_moneyline_raises(self):
        bad = {"events": [{"id": int(FID), "periods": [{"number": 0}]}]}
        with self.assertRaises(ProviderError):
            parse_pinnacle_snapshot(bad, "1X2", FID)

    def test_parse_event_not_found(self):
        with self.assertRaises(ProviderError):
            parse_pinnacle_snapshot(PINN_ODDS, "1X2", "999")

    def test_parse_settled(self):
        o = parse_pinnacle_settled(PINN_SETTLED, FID)
        self.assertEqual((o.status, o.home_goals, o.away_goals), ("COMPLETED", 2, 1))


class TestEndpointAndTransport(unittest.TestCase):
    def test_endpoint_builder(self):
        p = PinnacleLiveProvider(fm(), Transport(NullHttpClient()))
        spec = p._endpoint(FID, "1X2")
        self.assertEqual(spec.method, "GET")
        self.assertIn("/v1/odds", spec.url)
        self.assertEqual(spec.params["eventId"], FID)

    def test_fetch_snapshot_through_transport(self):
        p, t, audit = static_provider([HttpResponse(200, PINN_ODDS)])
        qs = p.fetch_snapshot("epl_m1", "1X2", "CLOSE")
        self.assertEqual({q.selection for q in qs}, {"HOME", "DRAW", "AWAY"})
        # auth was injected (deliverable 3) ...
        self.assertEqual(t.http.last_request["headers"]["Authorization"], f"Basic {SECRET}")
        # ... but the secret NEVER appears in the audit log
        self.assertNotIn(SECRET, json.dumps(audit.entries()))

    def test_fetch_outcome(self):
        p, _, _ = static_provider([HttpResponse(200, PINN_SETTLED)])
        o = p.fetch_outcome("epl_m1")
        self.assertEqual((o.home_goals, o.away_goals), (2, 1))


class TestErrorMapping(unittest.TestCase):
    def test_http_5xx_maps_to_provider_error(self):
        p, _, _ = static_provider([HttpResponse(500, {})])
        with self.assertRaises(ProviderError):
            p.fetch_snapshot("epl_m1", "1X2", "CLOSE")

    def test_network_error_maps_to_provider_error(self):
        p, _, _ = static_provider([ConnectionError("dns fail")])
        with self.assertRaises(ProviderError):
            p.fetch_snapshot("epl_m1", "1X2", "CLOSE")

    def test_missing_fixture_mapping_raises(self):
        p, _, _ = static_provider([HttpResponse(200, PINN_ODDS)])
        with self.assertRaises(ProviderError):
            p.fetch_snapshot("unmapped", "1X2", "CLOSE")


class TestConfigGated(unittest.TestCase):
    def test_default_factory_is_no_network(self):
        # make_pinnacle_provider with no http_client -> NullHttpClient -> raises
        p = make_pinnacle_provider(fm(), secret_provider=StaticSecretProvider({"PINNACLE_API_KEY": SECRET}))
        # patch a no-op sleeper transport? default factory sleeps; force max_retries=0 path:
        with self.assertRaises(ProviderError):
            # the binding goes through NullHttpClient; retry default would sleep, so use
            # a provider with a fast transport instead to assert the no-network behavior
            p.transport.retry = RetryPolicy(max_retries=0)
            p.transport.sleeper = lambda s: None
            p.fetch_snapshot("epl_m1", "1X2", "CLOSE")


class TestBridgeIntegration(unittest.TestCase):
    def test_plugs_into_ingestion_bridge(self):
        KO = datetime(2026, 5, 9, 15, 0, tzinfo=timezone.utc)
        clock = ManualClock(KO - timedelta(hours=100))
        sched = SnapshotScheduler(clock, ":memory:")
        sched.schedule_match("epl_m1", KO)
        truth = TruthStore(":memory:")
        # provider on a fake transport that always returns the odds payload
        _, t, _ = static_provider([HttpResponse(200, PINN_ODDS)])
        provider = PinnacleLiveProvider(fm(), t)
        bridge = IngestionBridge(sched, truth, [provider], db_path=":memory:")
        clock.set(KO)
        results = bridge.process_due()
        self.assertEqual(len(results), 7)
        self.assertTrue(all(r.status == "SUCCESS" for r in results))
        self.assertIsNotNone(truth.get_closing_truth("epl_m1", "1X2", "HOME"))
        bridge.close(); truth.close(); sched.close()


class TestAdditivity(unittest.TestCase):
    def test_m11_acceptance_hash_unchanged(self):
        # adding this adapter must not change the offline acceptance baseline
        import tests.test_m11_acceptance as m
        self.assertEqual(m.run_hash(m.baseline_providers()),
                         m.TestM11Acceptance.BASELINE_HASH)


if __name__ == "__main__":
    unittest.main(verbosity=2)
