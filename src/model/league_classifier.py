"""League Type Classification Layer.

Classifies leagues into:
  - EUROPE_STABLE
  - SUMMER_VOLATILE
  - HIGH_ROTATION
"""

import logging
from config.leagues import ALL_LEAGUES

logger = logging.getLogger(__name__)

# Config Overrides for league types
CONFIG_OVERRIDES = {
    "E0": "EUROPE_STABLE",
    "SP1": "EUROPE_STABLE",
    "D1": "EUROPE_STABLE",
    "I1": "EUROPE_STABLE",
    "T1": "EUROPE_STABLE",
    "F1": "EUROPE_STABLE",
    "NORWAY_ELITESERIEN": "SUMMER_VOLATILE",
    "BRAZIL_SERIE_A": "SUMMER_VOLATILE",
    "SWEDEN_ALLSVENSKAN": "SUMMER_VOLATILE",
    "USA_MLS": "SUMMER_VOLATILE",
}

# General fallback rules mapping based on known league codes
DEFAULT_RULES = {
    "E1": "EUROPE_STABLE",
    "E2": "EUROPE_STABLE",
    "E3": "EUROPE_STABLE",
    "SP2": "EUROPE_STABLE",
    "D2": "EUROPE_STABLE",
    "I2": "EUROPE_STABLE",
    "F2": "EUROPE_STABLE",
    "N1": "EUROPE_STABLE",
    "P1": "EUROPE_STABLE",
    "B1": "EUROPE_STABLE",
    "SC0": "EUROPE_STABLE",
    "G1": "EUROPE_STABLE",
    "AT1": "EUROPE_STABLE",
    "AT2": "EUROPE_STABLE",
}

class LeagueClassifier:
    """Classifies leagues into EUROPE_STABLE, SUMMER_VOLATILE, or HIGH_ROTATION."""

    def __init__(self, db=None):
        self.db = db

    def get_league_type(self, league_code: str) -> str:
        """Resolve league type using the hierarchy: DB Metadata -> Config Override -> Default Rules -> Fallback (HIGH_ROTATION)"""
        if not league_code:
            logger.warning("Empty league_code provided. Defaulting to HIGH_ROTATION.")
            return "HIGH_ROTATION"

        # 1. DB Metadata
        if self.db:
            try:
                if self.db.table_exists("league_metadata"):
                    row = self.db.fetchone(
                        "SELECT league_type FROM league_metadata WHERE league_code = ?",
                        (league_code,)
                    )
                    if row and row.get("league_type"):
                        return row["league_type"]
            except Exception as e:
                logger.warning(f"Failed to fetch league type from DB metadata for {league_code}: {e}")

        # 2. Config Override
        if league_code in CONFIG_OVERRIDES:
            return CONFIG_OVERRIDES[league_code]

        # 3. Default Rules
        if league_code in DEFAULT_RULES:
            return DEFAULT_RULES[league_code]

        # Rule-based fallback: Check country in config
        if league_code in ALL_LEAGUES:
            league = ALL_LEAGUES[league_code]
            if league.country in ["Norway", "Brazil", "Sweden", "Finland", "USA"]:
                return "SUMMER_VOLATILE"
            elif league.country in ["England", "Spain", "Germany", "Italy", "Turkey", "France", "Netherlands", "Portugal", "Belgium", "Scotland", "Greece", "Austria"]:
                return "EUROPE_STABLE"

        # 4. Unknown league: Fallback to HIGH_ROTATION with warning log
        logger.warning(f"Unknown league_code '{league_code}'. Classifying as 'HIGH_ROTATION'.")
        return "HIGH_ROTATION"
