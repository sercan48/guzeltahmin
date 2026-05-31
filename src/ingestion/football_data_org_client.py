"""football-data.org REST API client — free tier (10 req/min, 12 competitions)."""

import requests
import logging
from typing import Optional

from config.settings import FOOTBALL_DATA_ORG_KEY
from config.leagues import ACTIVE_LEAGUES

logger = logging.getLogger(__name__)

API_BASE = "https://api.football-data.org/v4"

LEAGUE_ID_MAP = {
    "E0": "PL",    # Premier League
    "SP1": "PD",   # Primera Division
    "D1": "BL1",   # Bundesliga
    "I1": "SA",    # Serie A
    "T1": "TSL",   # Süper Lig (paid tier only)
}


class FootballDataOrgClient:
    """Client for football-data.org API."""

    def __init__(self, api_key: str = ""):
        self.api_key = api_key or FOOTBALL_DATA_ORG_KEY
        self.session = requests.Session()
        if self.api_key:
            self.session.headers["X-Auth-Token"] = self.api_key

    def _get(self, endpoint: str, params: dict = None) -> Optional[dict]:
        url = f"{API_BASE}/{endpoint}"
        try:
            resp = self.session.get(url, params=params, timeout=15)
            if resp.status_code == 429:
                logger.warning("football-data.org rate limited")
                return None
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as e:
            logger.error(f"football-data.org error: {e}")
            return None

    def get_fixtures(self, league_code: str = "", date_from: str = "", date_to: str = "") -> Optional[list]:
        """Get upcoming/recent fixtures."""
        comp = LEAGUE_ID_MAP.get(league_code, "")
        if not comp:
            return None

        params = {}
        if date_from:
            params["dateFrom"] = date_from
        if date_to:
            params["dateTo"] = date_to

        data = self._get(f"competitions/{comp}/matches", params)
        if not data or "matches" not in data:
            return None

        matches = []
        for m in data["matches"]:
            matches.append({
                "id": m.get("id"),
                "date": m.get("utcDate", "")[:10],
                "home_team": m.get("homeTeam", {}).get("name", ""),
                "away_team": m.get("awayTeam", {}).get("name", ""),
                "status": m.get("status", ""),
                "home_goals": m.get("score", {}).get("fullTime", {}).get("home"),
                "away_goals": m.get("score", {}).get("fullTime", {}).get("away"),
                "matchday": m.get("matchday"),
                "league_code": league_code,
            })
        return matches

    def get_standings(self, league_code: str = "") -> Optional[list]:
        """Get current league standings."""
        comp = LEAGUE_ID_MAP.get(league_code, "")
        if not comp:
            return None

        data = self._get(f"competitions/{comp}/standings")
        if not data or "standings" not in data:
            return None

        table = data["standings"][0].get("table", []) if data["standings"] else []
        return [
            {
                "position": row.get("position"),
                "team": row.get("team", {}).get("name", ""),
                "played": row.get("playedGames", 0),
                "won": row.get("won", 0),
                "draw": row.get("draw", 0),
                "lost": row.get("lost", 0),
                "goals_for": row.get("goalsFor", 0),
                "goals_against": row.get("goalsAgainst", 0),
                "points": row.get("points", 0),
            }
            for row in table
        ]

    def get_results(self, league_code: str = "", **kwargs) -> Optional[list]:
        """Alias for get_fixtures with finished status."""
        return self.get_fixtures(league_code=league_code, **kwargs)

    def get_scorers(self, league_code: str = "") -> Optional[list]:
        """Get top scorers."""
        comp = LEAGUE_ID_MAP.get(league_code, "")
        if not comp:
            return None

        data = self._get(f"competitions/{comp}/scorers")
        if not data or "scorers" not in data:
            return None

        return [
            {
                "player": s.get("player", {}).get("name", ""),
                "team": s.get("team", {}).get("name", ""),
                "goals": s.get("goals", 0),
                "assists": s.get("assists", 0),
                "played": s.get("playedMatches", 0),
            }
            for s in data["scorers"]
        ]
