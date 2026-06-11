"""PHASE-LIVE L2 — Betfair production adapter.

A real Betfair Exchange provider on the existing ``OddsProvider`` ABC + M10.3
``Transport`` stack. Additive: ``src/market/activation/`` only; the fixture-based
``BetfairProvider`` and every downstream module are untouched. Network is OFF by
default (``NullHttpClient``); a real run is enabled ONLY via configuration.

Betfair specifics handled here (without touching transport.py):
- Exchange book: per-runner ``lastPriceTraded`` + ``totalMatched`` (liquidity).
- Runners are numeric ``selectionId``; a per-market runner map translates them to
  canonical HOME/DRAW/AWAY (built from listMarketCatalogue in production).
- Dual auth, both via HEADERS (never logged — the audit records no headers):
    X-Application:    app key   (injected by this adapter's endpoint)
    X-Authentication: session token (injected by Transport AuthConfig secret)
- ``marketId`` is the provider fixture id (FixtureMap: match_id -> Betfair marketId).

    PRODUCTION CONFIG EXAMPLE (not executed here)
    --------------------------------------------
    from src.market.activation import FixtureMap, UrllibHttpClient, EnvSecretProvider
    from src.market.activation.betfair_live import make_betfair_provider

    fm = FixtureMap(); fm.register("epl_m1", "betfair", "1.234")
    provider = make_betfair_provider(
        fm,
        http_client=UrllibHttpClient(timeout=8.0),     # enables real network
        secret_provider=EnvSecretProvider(),           # BETFAIR_APP_KEY + BETFAIR_SESSION_TOKEN
    )
    provider.register_runner("1.234", "47999", "HOME")  # selectionId -> canonical
    provider.register_runner("1.234", "47998", "DRAW")
    provider.register_runner("1.234", "47997", "AWAY")
    # drops into the UNCHANGED IngestionBridge(scheduler, truth_store, [provider], ...)

Field note (mirrors R1.1): confirm exact Betfair JSON-RPC field names against the
live account when enabling network; only this module changes. fetch_outcome maps
the winning runner to a 1X2-consistent score as a fallback — a dedicated results
vendor is preferred for true scores / O/U settlement.
"""

from __future__ import annotations

import json
from typing import Callable, Dict, List, Optional, Tuple

from .providers import OddsProvider, ProviderQuote, ProviderOutcome, ProviderError
from .fixture_map import FixtureMap
from .transport import (
    Transport, RequestSpec, HttpClient, NullHttpClient, SecretProvider,
    EnvSecretProvider, AuthConfig, RetryPolicy, RateLimiter, CircuitBreaker,
    RequestAuditLog,
)

_NAME = "betfair"
_CLASS = "SHARP"
RunnerLookup = Callable[[str, str], Optional[str]]


# ---------------------------------------------------------------------------
# Parsers (pure; offline-testable)
# ---------------------------------------------------------------------------
def _first_book(raw: dict) -> Optional[dict]:
    res = raw.get("result") if isinstance(raw, dict) else None
    if isinstance(res, list) and res:
        return res[0]
    if isinstance(raw, dict) and "runners" in raw:
        return raw
    return None


def parse_betfair_book(raw: dict, market: str, runner_lookup: RunnerLookup) -> List[ProviderQuote]:
    book = _first_book(raw)
    if book is None:
        raise ProviderError("betfair: empty market book")
    mid = str(book.get("marketId"))
    ts = book.get("publishTime")
    out: List[ProviderQuote] = []
    for r in book.get("runners", []):
        sel = runner_lookup(mid, str(r.get("selectionId")))
        if sel is None:
            continue
        price = r.get("lastPriceTraded")
        if price is None or float(price) <= 1.0:
            continue
        out.append(ProviderQuote(_NAME, market, sel, float(price), _CLASS,
                                 timestamp=ts, liquidity=r.get("totalMatched")))
    if not out:
        raise ProviderError("betfair: no priced/mapped runners")
    return sorted(out, key=lambda q: q.selection)


# winner selection -> 1X2-consistent (home, away) score
_WINNER_SCORE = {"HOME": (1, 0), "DRAW": (1, 1), "AWAY": (0, 1)}


def parse_betfair_settled(raw: dict, runner_lookup: RunnerLookup) -> Optional[ProviderOutcome]:
    book = _first_book(raw)
    if book is None:
        return None
    status = (book.get("status") or "").upper()
    if status in ("VOID", "CANCELLED"):
        return ProviderOutcome("CANCELLED", None, None)
    mid = str(book.get("marketId"))
    winner = None
    for r in book.get("runners", []):
        if (r.get("status") or "").upper() == "WINNER":
            winner = runner_lookup(mid, str(r.get("selectionId")))
            break
    if winner is None or winner not in _WINNER_SCORE:
        return None
    h, a = _WINNER_SCORE[winner]
    return ProviderOutcome("COMPLETED", h, a)


# ---------------------------------------------------------------------------
# Provider
# ---------------------------------------------------------------------------
class BetfairLiveProvider(OddsProvider):
    name = _NAME
    provider_class = _CLASS

    def __init__(self, fixture_map: FixtureMap, transport: Transport,
                 secret_provider: SecretProvider, app_key_secret: str = "BETFAIR_APP_KEY",
                 base_url: str = "https://api.betfair.com/exchange/betting") -> None:
        self.fixture_map = fixture_map
        self.secrets = secret_provider
        self.app_key_secret = app_key_secret
        self.base_url = base_url.rstrip("/")
        self._runners: Dict[Tuple[str, str], str] = {}
        self._call = transport.binding(self._endpoint)
        self.transport = transport

    # runner map (selectionId -> canonical selection), built from market catalogue
    def register_runner(self, market_id: str, selection_id: str, selection: str) -> None:
        self._runners[(str(market_id), str(selection_id))] = selection

    def _runner_lookup(self, market_id: str, selection_id: str) -> Optional[str]:
        return self._runners.get((str(market_id), str(selection_id)))

    # endpoint builder (JSON-RPC POST; app key header here, session token via Transport)
    def _endpoint(self, fixture_id: str, market: str) -> RequestSpec:
        method = ("SportsAPING/v1.0/listMarketBook")
        price_data = ["EX_TRADED"]
        rpc = {"jsonrpc": "2.0", "method": method, "id": 1,
               "params": {"marketIds": [fixture_id],
                          "priceProjection": {"priceData": price_data}}}
        return RequestSpec(
            "POST", f"{self.base_url}/json-rpc/v1",
            params={"_rpc": json.dumps(rpc, sort_keys=True), "marketId": fixture_id},
            headers={"X-Application": self.secrets.get(self.app_key_secret),
                     "Content-Type": "application/json"})

    def fetch_snapshot(self, match_id: str, market: str, tick: str) -> List[ProviderQuote]:
        fid = self._fixture(match_id)
        raw = self._call(fid, market)
        return parse_betfair_book(raw, market, self._runner_lookup)

    def fetch_outcome(self, match_id: str) -> Optional[ProviderOutcome]:
        fid = self._fixture(match_id)
        raw = self._call(fid, "__outcome__")
        return parse_betfair_settled(raw, self._runner_lookup)

    def _fixture(self, match_id: str) -> str:
        fid = self.fixture_map.to_provider(match_id, self.name)
        if fid is None:
            raise ProviderError(f"betfair: no fixture mapping for {match_id}")
        return fid

    def health(self) -> dict:
        return self.transport.health_snapshot()


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------
def make_betfair_provider(
    fixture_map: FixtureMap, *,
    base_url: str = "https://api.betfair.com/exchange/betting",
    http_client: Optional[HttpClient] = None,          # default: NO network
    secret_provider: Optional[SecretProvider] = None,
    session_secret: str = "BETFAIR_SESSION_TOKEN",
    app_key_secret: str = "BETFAIR_APP_KEY",
    rate_capacity: float = 1.0,
    rate_refill_per_sec: float = 0.5,                   # ~1 request / 2s
    retry: Optional[RetryPolicy] = None,
    breaker: Optional[CircuitBreaker] = None,
    audit: Optional[RequestAuditLog] = None,
) -> BetfairLiveProvider:
    sp = secret_provider or EnvSecretProvider()
    transport = Transport(
        http_client=http_client or NullHttpClient(),
        secret_provider=sp,
        auth=AuthConfig(secret_key=session_secret, header="X-Authentication"),
        retry=retry or RetryPolicy(max_retries=3, base_delay=1.0, factor=2.0, max_delay=16.0),
        rate_limiter=RateLimiter(rate_capacity, rate_refill_per_sec),
        breaker=breaker or CircuitBreaker(failure_threshold=5, cooldown=60.0),
        audit=audit or RequestAuditLog(),
    )
    return BetfairLiveProvider(fixture_map, transport, sp, app_key_secret, base_url)
