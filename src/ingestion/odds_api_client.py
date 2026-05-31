"""The Odds API client — live odds from multiple bookmakers."""

import requests
import logging
from typing import Optional

from config.settings import ODDS_API_KEY

logger = logging.getLogger(__name__)

API_BASE = "https://api.the-odds-api.com/v4"

SPORT_KEY_MAP = {
    "E0": "soccer_epl",
    "SP1": "soccer_spain_la_liga",
    "D1": "soccer_germany_bundesliga",
    "I1": "soccer_italy_serie_a",
    "T1": "soccer_turkey_super_league",
}


class OddsAPIClient:
    """Client for The Odds API (free: 500 credits/month)."""

    def __init__(self, api_key: str = ""):
        self.api_key = api_key or ODDS_API_KEY
        self.remaining_credits: Optional[int] = None
        self.session = requests.Session()

    def _get(self, endpoint: str, params: dict = None) -> Optional[dict]:
        if not self.api_key:
            logger.warning("ODDS_API_KEY not set")
            return None

        params = params or {}
        params["apiKey"] = self.api_key
        url = f"{API_BASE}/{endpoint}"

        try:
            resp = self.session.get(url, params=params, timeout=15)
            # Track remaining credits
            self.remaining_credits = int(resp.headers.get("x-requests-remaining", -1))
            if self.remaining_credits >= 0 and self.remaining_credits < 50:
                logger.warning(f"Odds API: only {self.remaining_credits} credits remaining!")

            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as e:
            logger.error(f"Odds API error: {e}")
            return None

    def get_odds(self, league_code: str = "", **kwargs) -> Optional[list]:
        """Get current odds for upcoming matches in a league."""
        sport_key = SPORT_KEY_MAP.get(league_code)
        if not sport_key:
            return None

        data = self._get(
            f"sports/{sport_key}/odds",
            params={
                "regions": "eu",
                "markets": "h2h,totals",
                "oddsFormat": "decimal",
            },
        )
        if not data:
            return None

        results = []
        for event in data:
            match_odds = {
                "home_team": event.get("home_team", ""),
                "away_team": event.get("away_team", ""),
                "commence_time": event.get("commence_time", ""),
                "bookmakers": [],
            }

            for bm in event.get("bookmakers", []):
                bm_data = {"name": bm.get("title", "")}
                for market in bm.get("markets", []):
                    if market["key"] == "h2h":
                        for outcome in market.get("outcomes", []):
                            if outcome["name"] == event.get("home_team"):
                                bm_data["home_odds"] = outcome["price"]
                            elif outcome["name"] == event.get("away_team"):
                                bm_data["away_odds"] = outcome["price"]
                            elif outcome["name"] == "Draw":
                                bm_data["draw_odds"] = outcome["price"]
                    elif market["key"] == "totals":
                        for outcome in market.get("outcomes", []):
                            if outcome["name"] == "Over":
                                bm_data["over25_odds"] = outcome["price"]
                            elif outcome["name"] == "Under":
                                bm_data["under25_odds"] = outcome["price"]
                match_odds["bookmakers"].append(bm_data)

            # Compute average odds across bookmakers
            avg = self._average_odds(match_odds["bookmakers"])
            match_odds["avg_odds"] = avg
            results.append(match_odds)

        logger.info(f"Odds API: {len(results)} events for {league_code}")
        return results

    def _average_odds(self, bookmakers: list) -> dict:
        keys = ["home_odds", "draw_odds", "away_odds", "over25_odds", "under25_odds"]
        avg = {}
        for key in keys:
            vals = [bm.get(key) for bm in bookmakers if bm.get(key)]
            avg[key] = round(sum(vals) / len(vals), 3) if vals else None
        return avg

    def get_available_sports(self) -> Optional[list]:
        """List all available sports/leagues."""
        data = self._get("sports")
        if not data:
            return None
        return [{"key": s["key"], "title": s["title"], "active": s["active"]} for s in data]
