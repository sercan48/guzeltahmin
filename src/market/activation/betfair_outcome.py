"""PHASE-LIVE — BetfairOutcomeProvider: dedicated settled-outcome provider.

Focused solely on match outcome resolution via Betfair JSON-RPC listMarketBook.
Separate from BetfairLiveProvider (odds) so outcome ingestion can be wired
independently (different schedule, different rate-limit budget).

Auth: dual header (X-Application app key + X-Authentication session token).
Liquidity: market-level ``totalMatched`` -> ``ProviderOutcome.liquidity``.
Network: OFF by default (NullHttpClient). Real run: inject UrllibHttpClient.

    PRODUCTION CONFIG EXAMPLE
    -------------------------
    from src.market.activation import FixtureMap, UrllibHttpClient, EnvSecretProvider
    from src.market.activation.betfair_outcome import make_betfair_outcome_provider

    fm = FixtureMap(); fm.register("epl_m1", "betfair_outcome", "1.234")
    provider = make_betfair_outcome_provider(
        fm,
        http_client=UrllibHttpClient(timeout=8.0),
        secret_provider=EnvSecretProvider(),
    )
    provider.register_runner("1.234", "47999", "HOME")
    provider.register_runner("1.234", "47998", "DRAW")
    provider.register_runner("1.234", "47997", "AWAY")
    # bridge.ingest_outcome(match_id) -> M8.1 SettlementLedger
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

_NAME = "betfair_outcome"
_CLASS = "SHARP"
RunnerLookup = Callable[[str, str], Optional[str]]

_WINNER_SCORE: Dict[str, Tuple[int, int]] = {
    "HOME": (1, 0), "DRAW": (1, 1), "AWAY": (0, 1),
}


# ---------------------------------------------------------------------------
# Parser (pure; offline-testable)
# ---------------------------------------------------------------------------
def parse_outcome_book(raw: dict, runner_lookup: RunnerLookup) -> Optional[ProviderOutcome]:
    """Parse a listMarketBook JSON-RPC response into a ProviderOutcome.

    Captures:
    - winner runner (selectionId -> canonical selection -> score)
    - market totalMatched -> ProviderOutcome.liquidity
    - VOID / CANCELLED / SUSPENDED -> CANCELLED
    - no WINNER runner found -> None (market not yet settled)
    """
    res = raw.get("result") if isinstance(raw, dict) else None
    books = res if isinstance(res, list) else ([raw] if isinstance(raw, dict) else [])
    book = books[0] if books else None
    if not book:
        return None

    status = (book.get("status") or "").upper()
    if status in ("VOID", "CANCELLED", "SUSPENDED"):
        return ProviderOutcome("CANCELLED", None, None, liquidity=None)

    mid = str(book.get("marketId", ""))
    total_matched = book.get("totalMatched")
    liquidity = float(total_matched) if total_matched is not None else None

    winner: Optional[str] = None
    for r in book.get("runners", []):
        if (r.get("status") or "").upper() == "WINNER":
            winner = runner_lookup(mid, str(r.get("selectionId", "")))
            break

    if winner is None:
        return None                    # not settled yet / unresolvable

    if winner not in _WINNER_SCORE:
        return None                    # unmapped winner selection

    h, a = _WINNER_SCORE[winner]
    return ProviderOutcome("COMPLETED", h, a, liquidity=liquidity)


# ---------------------------------------------------------------------------
# Provider
# ---------------------------------------------------------------------------
class BetfairOutcomeProvider(OddsProvider):
    """Outcome-only Betfair provider (ABC compliant; fetch_snapshot raises)."""

    name = _NAME
    provider_class = _CLASS

    def __init__(self, fixture_map: FixtureMap, transport: Transport,
                 secret_provider: SecretProvider,
                 app_key_secret: str = "BETFAIR_APP_KEY",
                 base_url: str = "https://api.betfair.com/exchange/betting") -> None:
        self.fixture_map = fixture_map
        self.secrets = secret_provider
        self.app_key_secret = app_key_secret
        self.base_url = base_url.rstrip("/")
        self._runners: Dict[Tuple[str, str], str] = {}
        self._call = transport.binding(self._endpoint)
        self.transport = transport

    def register_runner(self, market_id: str, selection_id: str, selection: str) -> None:
        self._runners[(str(market_id), str(selection_id))] = selection

    def _runner_lookup(self, market_id: str, selection_id: str) -> Optional[str]:
        return self._runners.get((str(market_id), str(selection_id)))

    # endpoint: listMarketBook with ORDER_STATUS projection for settlement
    def _endpoint(self, fixture_id: str, _market: str) -> RequestSpec:
        rpc = {"jsonrpc": "2.0", "method": "SportsAPING/v1.0/listMarketBook", "id": 1,
               "params": {"marketIds": [fixture_id],
                          "orderProjection": "ALL",
                          "priceProjection": {"priceData": ["EX_TRADED"]}}}
        return RequestSpec(
            "POST", f"{self.base_url}/json-rpc/v1",
            params={"_rpc": json.dumps(rpc, sort_keys=True), "marketId": fixture_id},
            headers={"X-Application": self.secrets.get(self.app_key_secret),
                     "Content-Type": "application/json"})

    def fetch_outcome(self, match_id: str) -> Optional[ProviderOutcome]:
        fid = self._fixture(match_id)
        raw = self._call(fid, "__outcome__")
        return parse_outcome_book(raw, self._runner_lookup)

    def fetch_snapshot(self, match_id: str, market: str, tick: str) -> List[ProviderQuote]:
        raise ProviderError(f"{_NAME}: outcome-only provider; fetch_snapshot not supported")

    def health(self) -> dict:
        return self.transport.health_snapshot()

    def _fixture(self, match_id: str) -> str:
        fid = self.fixture_map.to_provider(match_id, self.name)
        if fid is None:
            raise ProviderError(f"{_NAME}: no fixture mapping for {match_id}")
        return fid


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------
def make_betfair_outcome_provider(
    fixture_map: FixtureMap, *,
    base_url: str = "https://api.betfair.com/exchange/betting",
    http_client: Optional[HttpClient] = None,
    secret_provider: Optional[SecretProvider] = None,
    session_secret: str = "BETFAIR_SESSION_TOKEN",
    app_key_secret: str = "BETFAIR_APP_KEY",
    rate_capacity: float = 1.0,
    rate_refill_per_sec: float = 0.5,        # ~1 req / 2s
    retry: Optional[RetryPolicy] = None,
    breaker: Optional[CircuitBreaker] = None,
    audit: Optional[RequestAuditLog] = None,
) -> BetfairOutcomeProvider:
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
    return BetfairOutcomeProvider(fixture_map, transport, sp, app_key_secret, base_url)
