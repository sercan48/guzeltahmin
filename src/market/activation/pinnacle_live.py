"""PHASE-LIVE L1 — Pinnacle production adapter.

A real Pinnacle provider built on the existing ``OddsProvider`` ABC and the M10.3
``Transport`` stack (retry / backoff / rate-limit / circuit-breaker / secret /
audit). Additive: it does NOT touch the fixture-based ``PinnacleProvider`` or any
module outside ``src/market/activation/``.

Network is OFF by default (``NullHttpClient``). A real run is enabled ONLY through
configuration — pass ``UrllibHttpClient`` + ``EnvSecretProvider``:

    PRODUCTION CONFIG EXAMPLE (not executed here)
    --------------------------------------------
    from src.market.activation import (FixtureMap, UrllibHttpClient, EnvSecretProvider)
    from src.market.activation.pinnacle_live import make_pinnacle_provider

    fm = FixtureMap()
    fm.register("epl_m1", "pinnacle", "1568987154")          # system id -> Pinnacle eventId
    provider = make_pinnacle_provider(
        fm,
        base_url="https://api.pinnacle.com",
        http_client=UrllibHttpClient(timeout=8.0),           # <-- enables real network
        secret_provider=EnvSecretProvider(),                 # PINNACLE_API_KEY from env/secret mgr
    )
    # provider now drops into the UNCHANGED IngestionBridge:
    #   IngestionBridge(scheduler, truth_store, [provider], ...)

Pinnacle auth is HTTP Basic; the base64 ``user:password`` token is stored as the
secret ``PINNACLE_API_KEY`` and injected as ``Authorization: Basic <token>``.
The token is NEVER logged (M10.3 audit redaction).

Field note (mirrors R1.1): the Pinnacle v1 odds schema is parsed below
(leagues -> events -> periods[number==0] -> moneyline/totals). Confirm exact
field names against the live account when enabling network; only this module
changes.
"""

from __future__ import annotations

from typing import Dict, List, Optional

from .providers import OddsProvider, ProviderQuote, ProviderOutcome, ProviderError
from .fixture_map import FixtureMap
from .transport import (
    Transport, RequestSpec, HttpClient, NullHttpClient, SecretProvider,
    EnvSecretProvider, AuthConfig, RetryPolicy, RateLimiter, CircuitBreaker,
    RequestAuditLog,
)

_NAME = "pinnacle"
_CLASS = "SHARP"


# ---------------------------------------------------------------------------
# Parsers (pure; offline-testable)
# ---------------------------------------------------------------------------
def _find_event(raw: dict, fixture_id: str) -> Optional[dict]:
    if not isinstance(raw, dict):
        return None
    if str(raw.get("id")) == str(fixture_id) and "periods" in raw:
        return raw
    events: List[dict] = []
    for league in raw.get("leagues", []):
        events.extend(league.get("events", []))
    events.extend(raw.get("events", []))
    for ev in events:
        if str(ev.get("id")) == str(fixture_id):
            return ev
    return None


def _full_match_period(event: dict) -> Optional[dict]:
    return next((p for p in event.get("periods", []) if p.get("number") == 0), None)


def parse_pinnacle_snapshot(raw: dict, market: str, fixture_id: str) -> List[ProviderQuote]:
    event = _find_event(raw, fixture_id)
    if event is None:
        raise ProviderError(f"pinnacle: event {fixture_id} not in response")
    period = _full_match_period(event)
    if period is None:
        raise ProviderError("pinnacle: no full-match period")
    ts = period.get("cutoff")

    if market in ("1X2", "MATCH_ODDS"):
        ml = period.get("moneyline")
        if not ml:
            raise ProviderError("pinnacle: no moneyline for fixture")
        out: List[ProviderQuote] = []
        for sel, key in (("HOME", "home"), ("DRAW", "draw"), ("AWAY", "away")):
            o = ml.get(key)
            if o is not None and float(o) > 1.0:
                out.append(ProviderQuote(_NAME, "1X2", sel, float(o), _CLASS, timestamp=ts))
        if not out:
            raise ProviderError("pinnacle: empty moneyline")
        return out

    if market in ("O/U", "TOTALS"):
        out = []
        for t in period.get("totals", []) or []:
            pts = t.get("points")
            if t.get("over") and float(t["over"]) > 1.0:
                out.append(ProviderQuote(_NAME, "O/U", f"OVER_{pts}", float(t["over"]),
                                         _CLASS, timestamp=ts))
            if t.get("under") and float(t["under"]) > 1.0:
                out.append(ProviderQuote(_NAME, "O/U", f"UNDER_{pts}", float(t["under"]),
                                         _CLASS, timestamp=ts))
        return out

    raise ProviderError(f"pinnacle: unsupported market {market}")


def parse_pinnacle_settled(raw: dict, fixture_id: str) -> Optional[ProviderOutcome]:
    event = _find_event(raw, fixture_id)
    if event is None:
        return None
    period = _full_match_period(event) or {}
    score = period.get("score") or event.get("score")
    status = period.get("status") or event.get("status") or "COMPLETED"
    if not isinstance(status, str):
        status = "COMPLETED"
    if score is None:
        return None
    return ProviderOutcome(status, score.get("home"), score.get("away"))


# ---------------------------------------------------------------------------
# Provider
# ---------------------------------------------------------------------------
class PinnacleLiveProvider(OddsProvider):
    name = _NAME
    provider_class = _CLASS

    def __init__(self, fixture_map: FixtureMap, transport: Transport,
                 base_url: str = "https://api.pinnacle.com", sport_id: int = 29) -> None:
        self.fixture_map = fixture_map
        self.base_url = base_url.rstrip("/")
        self.sport_id = sport_id
        self._call = transport.binding(self._endpoint)
        self.transport = transport

    # endpoint builder (deliverable 1)
    def _endpoint(self, fixture_id: str, market: str) -> RequestSpec:
        if market == "__outcome__":
            return RequestSpec("GET", f"{self.base_url}/v1/fixtures/settled",
                               params={"sportId": str(self.sport_id), "eventId": fixture_id})
        return RequestSpec("GET", f"{self.base_url}/v1/odds",
                           params={"sportId": str(self.sport_id), "eventId": fixture_id,
                                   "oddsFormat": "Decimal"})

    def fetch_snapshot(self, match_id: str, market: str, tick: str) -> List[ProviderQuote]:
        fid = self._fixture(match_id)
        raw = self._call(fid, market)              # transport: retry/ratelimit/breaker/auth/audit
        return parse_pinnacle_snapshot(raw, market, fid)

    def fetch_outcome(self, match_id: str) -> Optional[ProviderOutcome]:
        fid = self._fixture(match_id)
        raw = self._call(fid, "__outcome__")
        return parse_pinnacle_settled(raw, fid)

    def _fixture(self, match_id: str) -> str:
        fid = self.fixture_map.to_provider(match_id, self.name)
        if fid is None:
            raise ProviderError(f"pinnacle: no fixture mapping for {match_id}")
        return fid

    def health(self) -> dict:
        return self.transport.health_snapshot()


# ---------------------------------------------------------------------------
# Factory: wires Transport + secrets + rate-limit + breaker (deliverables 3-6)
# ---------------------------------------------------------------------------
def make_pinnacle_provider(
    fixture_map: FixtureMap, *,
    base_url: str = "https://api.pinnacle.com",
    http_client: Optional[HttpClient] = None,        # default: NO network
    secret_provider: Optional[SecretProvider] = None,
    secret_key: str = "PINNACLE_API_KEY",
    sport_id: int = 29,
    rate_capacity: float = 1.0,
    rate_refill_per_sec: float = 0.2,                # ~1 request / 5s (Pinnacle-friendly)
    retry: Optional[RetryPolicy] = None,
    breaker: Optional[CircuitBreaker] = None,
    audit: Optional[RequestAuditLog] = None,
) -> PinnacleLiveProvider:
    transport = Transport(
        http_client=http_client or NullHttpClient(),
        secret_provider=secret_provider or EnvSecretProvider(),
        auth=AuthConfig(secret_key=secret_key, header="Authorization", prefix="Basic "),
        retry=retry or RetryPolicy(max_retries=3, base_delay=1.0, factor=2.0, max_delay=16.0),
        rate_limiter=RateLimiter(rate_capacity, rate_refill_per_sec),
        breaker=breaker or CircuitBreaker(failure_threshold=5, cooldown=60.0),
        audit=audit or RequestAuditLog(),
    )
    return PinnacleLiveProvider(fixture_map, transport, base_url=base_url, sport_id=sport_id)
