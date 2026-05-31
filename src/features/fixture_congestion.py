"""Fixture congestion and fatigue features."""

import logging
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


def compute_congestion_features(db, team_id: int, match_date: str,
                                 is_home: bool = True) -> dict:
    """Compute fixture congestion/fatigue features for a team."""
    side = "home" if is_home else "away"

    try:
        ref_date = datetime.strptime(match_date, "%Y-%m-%d")
    except (ValueError, TypeError):
        return _defaults(side)

    date_14 = (ref_date - timedelta(days=14)).strftime("%Y-%m-%d")
    date_30 = (ref_date - timedelta(days=30)).strftime("%Y-%m-%d")

    # Matches in last 14 days
    row_14 = db.fetchone("""
        SELECT COUNT(*) as cnt FROM matches
        WHERE (home_team_id = ? OR away_team_id = ?)
        AND date BETWEEN ? AND ? AND date < ?
    """, (team_id, team_id, date_14, match_date, match_date))
    matches_14 = row_14["cnt"] if row_14 else 0

    # Matches in last 30 days
    row_30 = db.fetchone("""
        SELECT COUNT(*) as cnt FROM matches
        WHERE (home_team_id = ? OR away_team_id = ?)
        AND date BETWEEN ? AND ? AND date < ?
    """, (team_id, team_id, date_30, match_date, match_date))
    matches_30 = row_30["cnt"] if row_30 else 0

    # Days since last match
    last_match = db.fetchone("""
        SELECT MAX(date) as last_date FROM matches
        WHERE (home_team_id = ? OR away_team_id = ?)
        AND date < ?
    """, (team_id, team_id, match_date))

    days_rest = 7  # default
    if last_match and last_match["last_date"]:
        try:
            last_dt = datetime.strptime(str(last_match["last_date"])[:10], "%Y-%m-%d")
            days_rest = (ref_date - last_dt).days
        except (ValueError, TypeError):
            pass

    # Congestion score: normalized (1.0 = very congested)
    congestion_score = min(matches_14 / 4.0, 1.5)
    is_congested = 1 if matches_14 >= 3 else 0

    return {
        f"{side}_congestion_score": round(congestion_score, 3),
        f"{side}_days_rest": days_rest,
        f"_{side}_matches_14": matches_14,
        f"_{side}_matches_30": matches_30,
        f"_{side}_is_congested": is_congested,
    }


def compute_congestion_advantage(home_features: dict, away_features: dict) -> float:
    """Congestion advantage = away_congestion - home_congestion.

    Positive = home team is more rested (advantage).
    """
    h = home_features.get("home_congestion_score", 0)
    a = away_features.get("away_congestion_score", 0)
    return round(a - h, 3)


def compute_match_congestion(db, home_team_id: int, away_team_id: int,
                              match_date: str) -> dict:
    """Compute all congestion features for a match."""
    home = compute_congestion_features(db, home_team_id, match_date, is_home=True)
    away = compute_congestion_features(db, away_team_id, match_date, is_home=False)
    advantage = compute_congestion_advantage(home, away)
    return {
        "home_congestion_score": home["home_congestion_score"],
        "away_congestion_score": away["away_congestion_score"],
        "congestion_advantage": advantage,
    }


def _defaults(side: str) -> dict:
    return {
        f"{side}_congestion_score": 0.0,
        f"{side}_days_rest": 7,
        f"_{side}_matches_14": 0,
        f"_{side}_matches_30": 0,
        f"_{side}_is_congested": 0,
    }
