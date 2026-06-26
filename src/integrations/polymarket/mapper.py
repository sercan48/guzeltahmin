"""
src/integrations/polymarket/mapper.py — PolymarketProvider

Implements MarketConsensusProvider for Polymarket.

Matching strategy:
  1. Iterate active markets (cached per session, TTL=1h)
  2. Prefer soccer/sports-tagged markets
  3. Fuzzy-score both team names against market question (rapidfuzz)
  4. Accept if average score ≥ MATCH_THRESHOLD
  5. On no soccer-tagged match, retry across all active markets with higher threshold

Observer-only: no prediction engine imports permitted.
"""
from __future__ import annotations

import json
import logging
import time

from rapidfuzz import fuzz

from src.integrations.base import MarketConsensusProvider, MarketInfo, MarketSnapshot
from src.integrations.polymarket import parser as _parser
from src.integrations.polymarket.client import ClobClient, GammaClient

logger = logging.getLogger(__name__)

# Fuzzy match thresholds (0-100)
_THRESHOLD_TAGGED = 55      # sports-tagged market
_THRESHOLD_UNTAGGED = 78    # no sport tag (conservative to avoid false positives)

# Keywords that indicate a soccer/football market
_SOCCER_KEYWORDS: frozenset[str] = frozenset({
    "soccer", "football", "premier league", "la liga", "bundesliga",
    "serie a", "ligue 1", "primeira liga", "eredivisie", "champions league",
    "europa league", "world cup", "fifa", "uefa", "epl", "mls",
    "süper lig", "super lig", "ligue 1",
})


class PolymarketProvider(MarketConsensusProvider):
    """
    Polymarket implementation of MarketConsensusProvider.

    Uses:
      - Gamma REST API  for market discovery and prices (no auth)
      - CLOB REST API   for bid/ask order book data (no auth)
    """

    def __init__(
        self,
        gamma_client: GammaClient | None = None,
        clob_client: ClobClient | None = None,
        cache_ttl_s: int = 3600,
    ) -> None:
        self._gamma = gamma_client or GammaClient()
        self._clob = clob_client or ClobClient()
        self._cache: list[dict] = []
        self._cache_ts: float = 0.0
        self._cache_ttl = cache_ttl_s

    @property
    def provider_name(self) -> str:
        return "polymarket"

    # ------------------------------------------------------------------
    # Cache management
    # ------------------------------------------------------------------

    def _refresh_cache(self) -> None:
        logger.info("Fetching active Polymarket markets...")
        self._cache = list(self._gamma.iter_all_markets(active=True))
        self._cache_ts = time.monotonic()
        logger.info("Market cache: %d active markets loaded", len(self._cache))

    def _ensure_cache(self) -> None:
        age = time.monotonic() - self._cache_ts
        if not self._cache or age > self._cache_ttl:
            self._refresh_cache()

    # ------------------------------------------------------------------
    # Classification helpers
    # ------------------------------------------------------------------

    def _is_soccer_market(self, market: dict) -> bool:
        question = (market.get("question") or "").lower()
        tags = market.get("tags") or []
        tag_slugs = {
            (t.get("slug") or "").lower()
            for t in (tags if isinstance(tags, list) else [])
        }
        return bool(_SOCCER_KEYWORDS & tag_slugs) or any(
            kw in question for kw in _SOCCER_KEYWORDS
        )

    def _match_score(self, market: dict, home: str, away: str) -> float:
        question = market.get("question") or ""
        score_h = fuzz.partial_ratio(home.lower(), question.lower())
        score_a = fuzz.partial_ratio(away.lower(), question.lower())
        return (score_h + score_a) / 2.0

    # ------------------------------------------------------------------
    # MarketConsensusProvider interface
    # ------------------------------------------------------------------

    def find_market(self, home: str, away: str, date: str) -> MarketInfo | None:
        """
        Find the best Polymarket market for a fixture.
        Returns None if no confident match found.
        """
        self._ensure_cache()

        # Pass 1: soccer-tagged markets (lower threshold)
        candidates: list[tuple[float, dict]] = []
        for market in self._cache:
            score = self._match_score(market, home, away)
            threshold = (
                _THRESHOLD_TAGGED if self._is_soccer_market(market)
                else _THRESHOLD_UNTAGGED
            )
            if score >= threshold:
                candidates.append((score, market))

        if not candidates:
            logger.debug("No Polymarket match for %s vs %s on %s", home, away, date)
            return None

        best_score, best = max(candidates, key=lambda x: x[0])
        logger.info(
            "Polymarket match: %s vs %s → '%s' (score=%.1f)",
            home, away, best.get("question"), best_score,
        )

        info = _parser.parse_market_info(best)
        info.matched_home = home
        info.matched_away = away
        info.match_date = date
        return info

    def get_snapshot(
        self,
        market: MarketInfo,
        source_type: str = "pre_match",
    ) -> MarketSnapshot | None:
        """Fetch current market data and return a MarketSnapshot."""
        raw = self._gamma.get_market(market.market_id)
        if not raw:
            logger.warning("Gamma API returned nothing for market %s", market.market_id)
            return None

        # Fetch CLOB bid/ask for each outcome token
        token_ids = _parser._parse_json_field(raw.get("clobTokenIds"))
        clob_data: dict[str, tuple[float | None, float | None]] = {}
        for tid in token_ids:
            if tid:
                clob_data[tid] = self._clob.get_best_bid_ask(tid)

        snap = _parser.parse_snapshot(
            raw,
            clob_data=clob_data,
            source_type=source_type,
            home_team=market.matched_home,
            away_team=market.matched_away,
        )
        if snap is not None:
            snap.matched_home = market.matched_home
            snap.matched_away = market.matched_away
            snap.match_date = market.match_date
        return snap
