"""Real-shaped provider adapters: PinnacleProvider, BetfairProvider.

These implement the SAME ``OddsProvider`` ABC that the ingestion bridge already
consumes — proving a licensed provider plugs in with zero downstream redesign.

Network is isolated behind an injectable ``transport`` callable
``transport(fixture_id: str, market: str) -> dict`` (and for outcomes
``transport(fixture_id, "__outcome__")``). The default transport raises (no
network), so tests inject a fake transport returning a real-shaped payload and
the pure ``_parse_*`` logic produces canonical ProviderQuotes. A production
deployment provides an HTTP transport that performs auth + rate-limit + backoff
*inside the adapter* — the bridge and downstream are untouched.

Id translation uses the FixtureMap (canonical match_id <-> provider fixture id).
"""

from __future__ import annotations

from typing import Callable, Dict, List, Optional

from .providers import OddsProvider, ProviderQuote, ProviderOutcome, ProviderError
from .fixture_map import FixtureMap

Transport = Callable[[str, str], dict]


def _no_network(fixture_id: str, market: str) -> dict:
    raise ProviderError("no transport configured (network disabled)")


class PinnacleProvider(OddsProvider):
    """Pinnacle (sharp book). Decimal odds; no per-runner liquidity."""

    def __init__(self, fixture_map: FixtureMap, transport: Optional[Transport] = None,
                 market_map: Optional[Dict[str, str]] = None) -> None:
        self.name = "pinnacle"
        self.provider_class = "SHARP"
        self.fixture_map = fixture_map
        self.transport = transport or _no_network
        self.market_map = market_map or {"1X2": "1X2", "O/U": "TOTALS"}

    def fetch_snapshot(self, match_id: str, market: str, tick: str) -> List[ProviderQuote]:
        fid = self.fixture_map.to_provider(match_id, self.name)
        if fid is None:
            raise ProviderError(f"pinnacle: no fixture mapping for {match_id}")
        raw = self.transport(fid, self.market_map.get(market, market))
        return self._parse_snapshot(raw, market)

    def fetch_outcome(self, match_id: str) -> Optional[ProviderOutcome]:
        fid = self.fixture_map.to_provider(match_id, self.name)
        if fid is None:
            raise ProviderError(f"pinnacle: no fixture mapping for {match_id}")
        raw = self.transport(fid, "__outcome__")
        return self._parse_outcome(raw)

    # -- pure parsers (offline-testable) -----------------------------------
    def _parse_snapshot(self, raw: dict, market: str) -> List[ProviderQuote]:
        ts = raw.get("lastUpdate")
        prices = raw.get("markets", {}).get(market, {})
        return [ProviderQuote(self.name, market, sel, float(odds), self.provider_class,
                              timestamp=ts, liquidity=None)
                for sel, odds in sorted(prices.items())]

    @staticmethod
    def _parse_outcome(raw: dict) -> Optional[ProviderOutcome]:
        if not raw or "status" not in raw:
            return None
        return ProviderOutcome(raw["status"], raw.get("home"), raw.get("away"))


class BetfairProvider(OddsProvider):
    """Betfair Exchange. Last-traded price + matched-volume liquidity."""

    def __init__(self, fixture_map: FixtureMap, transport: Optional[Transport] = None,
                 market_map: Optional[Dict[str, str]] = None) -> None:
        self.name = "betfair"
        self.provider_class = "SHARP"
        self.fixture_map = fixture_map
        self.transport = transport or _no_network
        self.market_map = market_map or {"1X2": "MATCH_ODDS", "O/U": "OVER_UNDER"}

    def fetch_snapshot(self, match_id: str, market: str, tick: str) -> List[ProviderQuote]:
        fid = self.fixture_map.to_provider(match_id, self.name)
        if fid is None:
            raise ProviderError(f"betfair: no fixture mapping for {match_id}")
        raw = self.transport(fid, self.market_map.get(market, market))
        return self._parse_snapshot(raw, market)

    def fetch_outcome(self, match_id: str) -> Optional[ProviderOutcome]:
        fid = self.fixture_map.to_provider(match_id, self.name)
        if fid is None:
            raise ProviderError(f"betfair: no fixture mapping for {match_id}")
        raw = self.transport(fid, "__outcome__")
        return self._parse_outcome(raw)

    def _parse_snapshot(self, raw: dict, market: str) -> List[ProviderQuote]:
        ts = raw.get("lastMatchTime")
        out: List[ProviderQuote] = []
        for r in raw.get("runners", []):
            price = r.get("lastPriceTraded")
            if price is None or float(price) <= 1.0:
                continue
            out.append(ProviderQuote(
                self.name, market, r["selection"], float(price), self.provider_class,
                timestamp=ts, liquidity=r.get("totalMatched")))
        return sorted(out, key=lambda q: q.selection)

    @staticmethod
    def _parse_outcome(raw: dict) -> Optional[ProviderOutcome]:
        if not raw or "status" not in raw:
            return None
        return ProviderOutcome(raw["status"], raw.get("home"), raw.get("away"))
