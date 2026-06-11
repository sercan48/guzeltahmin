"""M10.2 — Provider adapter interface + deterministic mock provider.

Provider-agnostic ingestion surface. NO network: the mock provider returns
fixture data deterministically, so the whole ingestion bridge is replay-safe.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Dict, List, Optional, Set


class ProviderError(Exception):
    """Raised when a provider fetch fails (drives the retry framework)."""


@dataclass(frozen=True)
class ProviderQuote:
    provider: str
    market: str
    selection: str
    odds: float
    provider_class: str = "SHARP"


@dataclass(frozen=True)
class ProviderOutcome:
    status: str            # COMPLETED / VOID / CANCELLED / ...
    home_goals: Optional[int]
    away_goals: Optional[int]


class OddsProvider(ABC):
    name: str = "abstract"
    provider_class: str = "SHARP"

    @abstractmethod
    def fetch_snapshot(self, match_id: str, market: str, tick: str) -> List[ProviderQuote]:
        ...

    @abstractmethod
    def fetch_outcome(self, match_id: str) -> Optional[ProviderOutcome]:
        ...


class MockOddsProvider(OddsProvider):
    """Deterministic, network-free provider backed by a fixture.

    odds_fixture: {match_id: {tick: {selection: odds}}}
    outcomes:     {match_id: ProviderOutcome}
    fail_matches: matches whose snapshot fetch raises ProviderError (retry tests)
    """

    def __init__(self, name: str, provider_class: str,
                 odds_fixture: Dict[str, Dict[str, Dict[str, float]]],
                 outcomes: Optional[Dict[str, ProviderOutcome]] = None,
                 fail_matches: Optional[Set[str]] = None) -> None:
        self.name = name
        self.provider_class = provider_class
        self._odds = odds_fixture
        self._outcomes = outcomes or {}
        self._fail = set(fail_matches or set())

    def fetch_snapshot(self, match_id: str, market: str, tick: str) -> List[ProviderQuote]:
        if match_id in self._fail:
            raise ProviderError(f"{self.name}: forced failure for {match_id}")
        sel_odds = self._odds.get(match_id, {}).get(tick, {})
        return [ProviderQuote(self.name, market, sel, odds, self.provider_class)
                for sel, odds in sorted(sel_odds.items())]

    def fetch_outcome(self, match_id: str) -> Optional[ProviderOutcome]:
        if match_id in self._fail:
            raise ProviderError(f"{self.name}: forced outcome failure for {match_id}")
        return self._outcomes.get(match_id)
