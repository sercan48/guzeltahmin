"""Understat xG data client — scrapes expected goals for 4 leagues (not Süper Lig)."""

import logging
from typing import Optional

from config.leagues import ACTIVE_LEAGUES

logger = logging.getLogger(__name__)

UNDERSTAT_LEAGUES = {
    "E0": "EPL",
    "SP1": "La_liga",
    "D1": "Bundesliga",
    "I1": "Serie_A",
}

SEASON_MAP = {
    "2021": 2020,
    "2122": 2021,
    "2223": 2022,
    "2324": 2023,
    "2425": 2024,
    "2526": 2025,
}


class UnderstatClient:
    """Fetch xG data from Understat via understatapi library."""

    def __init__(self):
        self._api = None

    def _ensure_api(self):
        if self._api is None:
            try:
                from understatapi import UnderstatClient as _USC
                self._api = _USC()
            except ImportError:
                logger.error("understatapi not installed. Run: pip install understatapi")
                raise

    def get_xg(self, league_code: str = "", season: str = "", **kwargs) -> Optional[list]:
        """Get match-level xG data for a league-season."""
        us_league = UNDERSTAT_LEAGUES.get(league_code)
        if not us_league:
            logger.info(f"No Understat data for {league_code}, using implied xG")
            return None

        us_season = SEASON_MAP.get(season)
        if us_season is None:
            logger.warning(f"Unknown season mapping: {season}")
            return None

        self._ensure_api()
        try:
            results = self._api.league(league=us_league).get_match_data(season=str(us_season))
            matches = []
            for m in results:
                matches.append({
                    "date": m.get("datetime", "")[:10],
                    "home_team": m.get("h", {}).get("title", ""),
                    "away_team": m.get("a", {}).get("title", ""),
                    "home_xg": float(m.get("xG", {}).get("h", 0)),
                    "away_xg": float(m.get("xG", {}).get("a", 0)),
                    "home_goals": int(m.get("goals", {}).get("h", 0)),
                    "away_goals": int(m.get("goals", {}).get("a", 0)),
                    "league_code": league_code,
                    "season": season,
                })
            logger.info(f"Understat: {len(matches)} matches for {us_league} {us_season}")
            return matches
        except Exception as e:
            logger.error(f"Understat fetch failed: {e}")
            return None

    def get_team_xg(self, league_code: str, season: str, team_name: str) -> Optional[dict]:
        """Get team-level aggregated xG stats."""
        matches = self.get_xg(league_code=league_code, season=season)
        if not matches:
            return None

        home_xg, away_xg, home_goals, away_goals = [], [], [], []
        for m in matches:
            if m["home_team"] == team_name:
                home_xg.append(m["home_xg"])
                home_goals.append(m["home_goals"])
            elif m["away_team"] == team_name:
                away_xg.append(m["away_xg"])
                away_goals.append(m["away_goals"])

        total_xg = home_xg + away_xg
        total_goals = home_goals + away_goals
        if not total_xg:
            return None

        avg_xg = sum(total_xg) / len(total_xg)
        avg_goals = sum(total_goals) / len(total_goals)

        return {
            "team": team_name,
            "matches": len(total_xg),
            "avg_xg": round(avg_xg, 3),
            "avg_goals": round(avg_goals, 3),
            "xg_overperformance": round(avg_goals - avg_xg, 3),
        }


def compute_implied_xg(home_odds: float, away_odds: float, draw_odds: float,
                        home_goals_avg: float, away_goals_avg: float) -> tuple[float, float]:
    """Fallback: compute implied xG from odds + historical goal averages.

    Used for Süper Lig where Understat has no data.
    """
    if not all([home_odds, away_odds, draw_odds]):
        return home_goals_avg, away_goals_avg

    # Convert odds to implied probabilities
    total = (1/home_odds) + (1/draw_odds) + (1/away_odds)
    home_prob = (1/home_odds) / total
    away_prob = (1/away_odds) / total

    # Weight historical averages by implied probability
    league_avg_goals = 2.6  # typical per match
    home_xg = home_goals_avg * 0.6 + (home_prob * league_avg_goals) * 0.4
    away_xg = away_goals_avg * 0.6 + (away_prob * league_avg_goals) * 0.4

    return round(home_xg, 3), round(away_xg, 3)
