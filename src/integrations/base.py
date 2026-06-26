"""
src/integrations/base.py — Market Consensus Observer Interface

Abstract interface for external market consensus providers.
OBSERVER ONLY — must not influence, import, or modify any prediction engine component.

Future implementations:
  PolymarketProvider  (live — WP-POLY-1)
  BetfairProvider     (planned)
  PinnacleProvider    (planned)
  OddsApiProvider     (planned)

Data flow (read-only):
  Fixture (home, away, date)
       ↓  find_market()
  MarketInfo (event_id, market_id, question, slug, status)
       ↓  get_snapshot()
  MarketSnapshot (home_prob, draw_prob, away_prob, volume, liquidity, ...)
       ↓  stored to data/polymarket/
  BenchmarkRecord (model vs market vs actual — post-settlement)
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class MarketInfo:
    """Identifies a single market on an external consensus provider."""
    provider: str        # "polymarket" | "betfair" | "pinnacle" | "odds_api"
    event_id: str        # provider-specific event identifier
    market_id: str       # provider-specific market identifier
    question: str        # market question text as returned by provider
    slug: str            # URL slug
    status: str          # "active" | "closed" | "resolved"
    matched_home: str    # internal fixture home team name
    matched_away: str    # internal fixture away team name
    match_date: str      # YYYY-MM-DD


@dataclass
class OutcomeSnapshot:
    """Snapshot of a single outcome within a market (e.g., HOME WIN)."""
    label: str               # raw label from provider (e.g., "Home", "Yes", "1")
    role: str | None         # classified: "HOME" | "DRAW" | "AWAY" | None if unknown
    mid_price: float         # implied probability 0-1
    best_bid: float | None   # best bid price 0-1
    best_ask: float | None   # best ask price 0-1
    spread: float | None     # ask - bid (None if order book unavailable)


@dataclass
class MarketSnapshot:
    """
    Point-in-time snapshot of market consensus probabilities.

    Closing snapshots (is_closing=True) are immutable once written — they
    serve as the benchmark reference and must never be overwritten.
    """
    provider: str
    market_id: str
    home_prob: float | None         # 0-1 implied probability (HOME WIN)
    draw_prob: float | None         # 0-1 implied probability (DRAW); None for binary markets
    away_prob: float | None         # 0-1 implied probability (AWAY WIN)
    outcomes: list[OutcomeSnapshot]
    volume_24h: float | None        # USD volume (last 24h)
    liquidity: float | None         # USD available liquidity
    open_interest: float | None     # USD open interest (if available)
    timestamp: str                  # ISO 8601 UTC capture time
    is_closing: bool                # True = closing snapshot (immutable once written)
    source_type: str                # "pre_match" | "closing"
    matched_home: str = ""          # set by mapper
    matched_away: str = ""          # set by mapper
    match_date: str = ""            # set by mapper


@dataclass
class BenchmarkRecord:
    """
    One settled fixture compared across model prediction and market consensus.
    Produced post-settlement — does NOT influence future predictions.
    """
    natural_key: str         # "{home_lower}|{away_lower}|{date}"
    home_team: str
    away_team: str
    match_date: str
    actual_outcome: str      # "HOME_WIN" | "DRAW" | "AWAY_WIN"

    # Model outputs (read from shadow_settlements.jsonl)
    model_h: float           # 0-100 percentage
    model_d: float
    model_a: float
    model_prediction: str
    model_confidence: float
    model_correct: bool
    model_brier: float | None
    model_abs_error: float | None

    # Market consensus (read from closing_snapshots.jsonl)
    market_h: float | None   # 0-100 percentage (None if no snapshot)
    market_d: float | None
    market_a: float | None
    market_prediction: str | None
    market_correct: bool | None
    market_brier: float | None
    market_abs_error: float | None
    market_source: str       # "polymarket" | "betfair" | ...

    # Deltas: model% − market% (positive = model assigns higher probability)
    delta_h: float | None
    delta_d: float | None
    delta_a: float | None

    closing_snapshot_id: str = ""


class MarketConsensusProvider(ABC):
    """
    Abstract interface for external market consensus data.

    Implementations must be completely isolated from the prediction engine.
    No prediction module may import from this interface or its implementations.
    """

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Unique provider identifier (lowercase, no spaces)."""
        ...

    @abstractmethod
    def find_market(self, home: str, away: str, date: str) -> MarketInfo | None:
        """
        Find the matching market for a fixture.

        Args:
            home: Home team name (internal fixture name)
            away: Away team name
            date: Match date YYYY-MM-DD

        Returns:
            MarketInfo if a confident match is found, else None.
            Must never raise — return None on any failure.
        """
        ...

    @abstractmethod
    def get_snapshot(
        self,
        market: MarketInfo,
        source_type: str = "pre_match",
    ) -> MarketSnapshot | None:
        """
        Fetch a market consensus snapshot.

        Args:
            market: MarketInfo as returned by find_market()
            source_type: "pre_match" or "closing"

        Returns:
            MarketSnapshot, or None if market is unavailable.
            Must never raise — return None on any failure.
        """
        ...
