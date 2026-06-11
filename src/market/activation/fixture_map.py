"""Fixture mapping layer (provider activation).

Translates the system's canonical ``match_id`` to/from each provider's own
fixture/market id, so a real provider adapter resolves ids inside itself without
the bridge or any downstream module knowing provider-specific identifiers.

Pure-stdlib, deterministic, additive — no downstream coupling.
"""

from __future__ import annotations

from typing import Dict, Optional, Tuple


class FixtureMap:
    """Bidirectional (match_id, provider) <-> provider_fixture_id registry."""

    def __init__(self) -> None:
        self._to_provider: Dict[Tuple[str, str], str] = {}   # (match, provider) -> fixture id
        self._to_match: Dict[Tuple[str, str], str] = {}      # (provider, fixture id) -> match

    def register(self, match_id: str, provider: str, provider_fixture_id: str) -> None:
        self._to_provider[(match_id, provider)] = provider_fixture_id
        self._to_match[(provider, provider_fixture_id)] = match_id

    def to_provider(self, match_id: str, provider: str) -> Optional[str]:
        return self._to_provider.get((match_id, provider))

    def to_match(self, provider: str, provider_fixture_id: str) -> Optional[str]:
        return self._to_match.get((provider, provider_fixture_id))

    def has(self, match_id: str, provider: str) -> bool:
        return (match_id, provider) in self._to_provider
