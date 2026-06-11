"""M10.3 production transport layer invariants. No real network; deterministic."""

import json
import unittest

from src.market.activation import (
    Transport, RequestSpec, HttpResponse, HttpClient, NullHttpClient, FakeHttpClient,
    UrllibHttpClient, StaticSecretProvider, AuthConfig, RetryPolicy, RateLimiter,
    CircuitBreaker, CircuitState, RequestAuditLog, ProviderError,
    PinnacleProvider, FixtureMap,
)


class FakeClock:
    def __init__(self, start=0.0):
        self.t = start

    def __call__(self):
        return self.t

    def tick(self, d):
        self.t += d


def rec_sleeper(clock=None):
    calls = []

    def sleep(s):
        calls.append(s)
        if clock is not None:
            clock.tick(s)         # sleeping advances logical time
    sleep.calls = calls
    return sleep


def spec():
    return RequestSpec("GET", "https://api.example/odds", params={"fid": "1"})


def ok(body=None):
    return HttpResponse(200, body or {"markets": {"1X2": {"HOME": 2.0}}})


class TestNetworkAgnostic(unittest.TestCase):
    def test_default_is_no_network(self):
        # default HttpClient is NullHttpClient -> the core never reaches a network
        t = Transport(retry=RetryPolicy(max_retries=0), sleeper=lambda s: None)
        with self.assertRaises(ProviderError):
            t.request(spec())

    def test_urllib_client_swappable(self):
        self.assertTrue(issubclass(UrllibHttpClient, HttpClient))


class TestRetryBackoff(unittest.TestCase):
    def test_retry_then_success(self):
        http = FakeHttpClient([HttpResponse(503, {}), HttpResponse(503, {}), ok()])
        sl = rec_sleeper()
        t = Transport(http, retry=RetryPolicy(max_retries=3, base_delay=1, factor=2),
                      sleeper=sl)
        body = t.request(spec())
        self.assertIn("markets", body)
        self.assertEqual(sl.calls, [1.0, 2.0])      # exponential: 1*2^0, 1*2^1
        self.assertEqual(t.health.success, 1)
        self.assertEqual(t.health.failure, 2)

    def test_backoff_capped(self):
        http = FakeHttpClient([HttpResponse(500, {})])
        sl = rec_sleeper()
        t = Transport(http, retry=RetryPolicy(max_retries=4, base_delay=1, factor=10,
                                              max_delay=20), sleeper=sl)
        with self.assertRaises(ProviderError):
            t.request(spec())
        self.assertEqual(sl.calls, [1.0, 10.0, 20.0, 20.0])   # capped at max_delay

    def test_retry_after_header(self):
        http = FakeHttpClient([HttpResponse(429, {}, {"Retry-After": "5"}), ok()])
        sl = rec_sleeper()
        t = Transport(http, retry=RetryPolicy(max_retries=2, base_delay=1), sleeper=sl)
        t.request(spec())
        self.assertEqual(sl.calls, [5.0])           # honored Retry-After

    def test_non_retryable_4xx(self):
        http = FakeHttpClient([HttpResponse(404, {})])
        sl = rec_sleeper()
        t = Transport(http, retry=RetryPolicy(max_retries=3), sleeper=sl)
        with self.assertRaises(ProviderError):
            t.request(spec())
        self.assertEqual(sl.calls, [])              # no retry on client error


class TestRateLimiter(unittest.TestCase):
    def test_waits_when_exhausted(self):
        clock = FakeClock()
        http = FakeHttpClient([ok()])
        sl = rec_sleeper(clock)
        rl = RateLimiter(capacity=1, refill_per_sec=1.0, monotonic=clock)
        t = Transport(http, rate_limiter=rl, monotonic=clock, sleeper=sl)
        t.request(spec())                # token available -> no wait
        t.request(spec())                # exhausted -> must wait
        self.assertTrue(any(c > 0 for c in sl.calls))


class TestCircuitBreaker(unittest.TestCase):
    def test_opens_and_fast_fails_then_recovers(self):
        clock = FakeClock()
        http = FakeHttpClient([HttpResponse(500, {})])
        br = CircuitBreaker(failure_threshold=2, cooldown=10, monotonic=clock)
        t = Transport(http, retry=RetryPolicy(max_retries=0), breaker=br,
                      monotonic=clock, sleeper=rec_sleeper(clock))
        for _ in range(2):
            with self.assertRaises(ProviderError):
                t.request(spec())
        self.assertEqual(br.state, CircuitState.OPEN)
        calls_before = http.calls
        with self.assertRaises(ProviderError):
            t.request(spec())            # fast-fail, no HTTP
        self.assertEqual(http.calls, calls_before)
        clock.tick(11)                   # past cooldown
        self.assertTrue(br.allow())
        self.assertEqual(br.state, CircuitState.HALF_OPEN)


class TestSecretRedaction(unittest.TestCase):
    SECRET = "SUPERSECRET_PINN_123"

    def test_secret_in_header_not_in_logs(self):
        http = FakeHttpClient([ok()])
        audit = RequestAuditLog()
        t = Transport(http, secret_provider=StaticSecretProvider({"K": self.SECRET}),
                      auth=AuthConfig(secret_key="K", header="Authorization", prefix="Bearer "),
                      audit=audit)
        t.request(spec())
        # the secret WAS sent to the http client (auth works)...
        self.assertEqual(http.last_request["headers"]["Authorization"], f"Bearer {self.SECRET}")
        # ...but NEVER appears in the audit log
        self.assertNotIn(self.SECRET, json.dumps(audit.entries()))

    def test_secret_in_query_param_redacted_in_audit(self):
        http = FakeHttpClient([ok()])
        audit = RequestAuditLog()
        t = Transport(http, secret_provider=StaticSecretProvider({"K": self.SECRET}),
                      auth=AuthConfig(secret_key="K", param="apiKey"), audit=audit)
        t.request(spec())
        self.assertEqual(http.last_request["params"]["apiKey"], self.SECRET)
        dumped = json.dumps(audit.entries())
        self.assertNotIn(self.SECRET, dumped)
        self.assertIn("***", dumped)


class TestHealthAndAudit(unittest.TestCase):
    def test_health_snapshot(self):
        http = FakeHttpClient([ok()])
        t = Transport(http)
        t.request(spec())
        snap = t.health_snapshot()
        for k in ("success", "failure", "avg_latency_ms", "breaker_state"):
            self.assertIn(k, snap)
        self.assertEqual(snap["success"], 1)
        self.assertEqual(snap["breaker_state"], "CLOSED")

    def test_audit_records_outcomes(self):
        http = FakeHttpClient([HttpResponse(503, {}), ok()])
        audit = RequestAuditLog()
        t = Transport(http, retry=RetryPolicy(max_retries=1), sleeper=lambda s: None, audit=audit)
        t.request(spec())
        outcomes = [e["outcome"] for e in audit.entries()]
        self.assertEqual(outcomes, ["retryable", "success"])


class TestProviderDIThroughTransport(unittest.TestCase):
    def test_pinnacle_consumes_transport_binding(self):
        fm = FixtureMap()
        fm.register("m", "pinnacle", "P-1")
        payload = {"lastUpdate": "2026-03-01T17:58:00+00:00",
                   "markets": {"1X2": {"HOME": 2.0, "DRAW": 3.4, "AWAY": 3.6}}}
        http = FakeHttpClient([HttpResponse(200, payload)])
        engine = Transport(http)

        def endpoint(fid, market):
            return RequestSpec("GET", f"https://api.pinnacle/fixtures/{fid}/odds",
                               params={"market": market})

        provider = PinnacleProvider(fm, transport=engine.binding(endpoint))
        quotes = provider.fetch_snapshot("m", "1X2", "CLOSE")
        self.assertEqual({q.selection for q in quotes}, {"HOME", "DRAW", "AWAY"})
        self.assertTrue(all(q.timestamp == "2026-03-01T17:58:00+00:00" for q in quotes))

    def test_offline_function_transport_still_works(self):
        # the plain-callable DI seam (no production stack) is unaffected
        fm = FixtureMap()
        fm.register("m", "pinnacle", "P-1")
        provider = PinnacleProvider(fm, transport=lambda fid, mkt: {
            "lastUpdate": "2026-03-01T17:58:00+00:00",
            "markets": {"1X2": {"HOME": 2.0, "DRAW": 3.4, "AWAY": 3.6}}})
        self.assertEqual(len(provider.fetch_snapshot("m", "1X2", "CLOSE")), 3)


if __name__ == "__main__":
    unittest.main(verbosity=2)
