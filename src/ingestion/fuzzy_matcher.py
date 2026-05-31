"""Fuzzy matching for team and player names across data sources."""

from rapidfuzz import fuzz, process
from typing import Optional

from config.constants import FUZZY_MATCH_THRESHOLD


class FuzzyMatcher:
    """Match team/player names across Football-Data, FIFA, and API sources.

    Uses rapidfuzz for fast C++-based fuzzy string matching.
    Maintains a manual override table for known mismatches.
    """

    def __init__(self):
        self._overrides: dict[str, str] = {}
        self._cache: dict[str, str] = {}
        self._load_default_overrides()

    def _load_default_overrides(self):
        """Known name mappings across data sources."""
        self._overrides = {
            # Football-Data → Standard Name
            "Man United": "Manchester United",
            "Man City": "Manchester City",
            "Tottenham": "Tottenham Hotspur",
            "Newcastle": "Newcastle United",
            "Wolves": "Wolverhampton Wanderers",
            "Nott'm Forest": "Nottingham Forest",
            "Sheffield Utd": "Sheffield United",
            "West Ham": "West Ham United",
            "Ipswich": "Ipswich Town",
            "Leicester": "Leicester City",
            "Leeds": "Leeds United",
            "Norwich": "Norwich City",
            # La Liga
            "Ath Madrid": "Atletico Madrid",
            "Ath Bilbao": "Athletic Bilbao",
            "Betis": "Real Betis",
            "Sociedad": "Real Sociedad",
            "Vallecano": "Rayo Vallecano",
            "La Coruna": "Deportivo La Coruna",
            # Bundesliga
            "Dortmund": "Borussia Dortmund",
            "M'gladbach": "Borussia Monchengladbach",
            "Leverkusen": "Bayer Leverkusen",
            "Bayern Munich": "Bayern München",
            "Ein Frankfurt": "Eintracht Frankfurt",
            # Serie A
            "Inter": "Inter Milan",
            "AC Milan": "AC Milan",
            "Verona": "Hellas Verona",
            # Ligue 1
            "Paris SG": "Paris Saint-Germain",
            "St Etienne": "Saint-Etienne",
            # Türkiye
            "Galatasaray": "Galatasaray",
            "Fenerbahce": "Fenerbahçe",
            "Besiktas": "Beşiktaş",
            "Trabzonspor": "Trabzonspor",
            "Basaksehir": "Istanbul Başakşehir",
            "Kasimpasa": "Kasımpaşa",
            "Gaziantep FK": "Gaziantep FK",
            "Antalyaspor": "Antalyaspor",
            "Konyaspor": "Konyaspor",
            "Sivasspor": "Sivasspor",
        }

    def add_override(self, source_name: str, standard_name: str):
        """Add a manual name mapping."""
        self._overrides[source_name] = standard_name

    def match(self, name: str, candidates: list[str], threshold: int = None) -> Optional[str]:
        """Find the best matching name from a list of candidates.

        Args:
            name: Name to match
            candidates: List of possible matches
            threshold: Minimum similarity score (0-100)

        Returns:
            Best matching candidate name, or None if below threshold.
        """
        if threshold is None:
            threshold = FUZZY_MATCH_THRESHOLD

        # Check cache first
        cache_key = f"{name}::{','.join(sorted(candidates[:10]))}"
        if cache_key in self._cache:
            return self._cache[cache_key]

        # Check overrides
        if name in self._overrides:
            override = self._overrides[name]
            if override in candidates:
                self._cache[cache_key] = override
                return override

        # Exact match
        if name in candidates:
            self._cache[cache_key] = name
            return name

        # Fuzzy match
        result = process.extractOne(
            name, candidates,
            scorer=fuzz.token_sort_ratio,
            score_cutoff=threshold,
        )

        if result:
            matched_name = result[0]
            self._cache[cache_key] = matched_name
            return matched_name

        return None

    def match_teams(self, source_teams: list[str], db_teams: list[str]) -> dict[str, str]:
        """Match all source team names to database team names.

        Returns:
            Dict mapping source_name → db_name
        """
        mapping = {}
        unmatched = []

        for team in source_teams:
            matched = self.match(team, db_teams)
            if matched:
                mapping[team] = matched
            else:
                unmatched.append(team)

        if unmatched:
            print(f"[WARN] {len(unmatched)} unmatched teams: {unmatched[:10]}")

        return mapping

    def clear_cache(self):
        """Clear the matching cache."""
        self._cache.clear()


# Singleton instance
matcher = FuzzyMatcher()
