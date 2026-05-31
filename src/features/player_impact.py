"""Position-based player impact scoring using FIFA attributes."""

import pandas as pd
import numpy as np

from config.constants import POSITION_WEIGHTS


def player_score(player: dict) -> float:
    """Calculate a single player's impact score based on position.

    Uses position-specific metric weights from constants.
    GK scored differently than FWD, etc.
    """
    position = player.get("position", "MID")
    weights = POSITION_WEIGHTS.get(position, POSITION_WEIGHTS["MID"])

    score = 0.0

    # Primary metrics
    for attr, weight in weights["primary"].items():
        key = f"fifa_{attr}"
        val = player.get(key, 50)
        score += val * weight

    # Secondary metrics
    for attr, weight in weights["secondary"].items():
        key = f"fifa_{attr}"
        val = player.get(key, 50)
        score += val * weight

    return score


def importance_score(player_rating: float, team_avg: float, team_std: float) -> float:
    """Calculate how 'irreplaceable' a player is to their team.

    Uses z-score: higher = more important.
    A player with rating far above team average is harder to replace.
    """
    if team_std == 0:
        return 0.0
    return (player_rating - team_avg) / team_std


def team_attack_rating(players: list[dict]) -> float:
    """Calculate team's attacking strength from FWD and MID players."""
    attackers = [p for p in players if p.get("position") in ("FWD", "MID")]
    if not attackers:
        return 50.0  # Neutral

    scores = []
    for p in attackers:
        shooting = p.get("fifa_shooting", 50)
        pace = p.get("fifa_pace", 50)
        dribbling = p.get("fifa_dribbling", 50)
        scores.append(shooting * 0.5 + pace * 0.25 + dribbling * 0.25)

    return np.mean(scores)


def team_defense_rating(players: list[dict]) -> float:
    """Calculate team's defensive strength from DEF and GK players."""
    defenders = [p for p in players if p.get("position") in ("DEF", "GK")]
    if not defenders:
        return 50.0

    scores = []
    for p in defenders:
        defending = p.get("fifa_defending", 50)
        physical = p.get("fifa_physical", 50)
        scores.append(defending * 0.6 + physical * 0.4)

    return np.mean(scores)


def squad_overall_rating(players: list[dict]) -> float:
    """Calculate weighted squad rating considering position importance."""
    if not players:
        return 50.0

    weighted_sum = 0.0
    total_weight = 0.0

    for p in players:
        pos = p.get("position", "MID")
        team_weight = POSITION_WEIGHTS.get(pos, POSITION_WEIGHTS["MID"])["team_weight"]
        score = player_score(p)
        weighted_sum += score * team_weight
        total_weight += team_weight

    return weighted_sum / total_weight if total_weight > 0 else 50.0


def squad_market_value(players: list[dict]) -> float:
    """Sum of all players' market values in millions."""
    return sum(p.get("market_value", 0) for p in players)


def load_team_players(db, team_id: int) -> list[dict]:
    """Load all players for a team from database."""
    rows = db.fetchall(
        """SELECT name, position, fifa_overall, fifa_pace, fifa_shooting,
                  fifa_passing, fifa_dribbling, fifa_defending, fifa_physical,
                  market_value, importance_score
        FROM players WHERE team_id=?""",
        (team_id,),
    )
    return [dict(r) for r in rows]


def calculate_importance_scores(db, team_id: int):
    """Calculate and store importance scores for all players in a team."""
    players = load_team_players(db, team_id)
    if not players:
        return

    # Group by position
    by_position = {}
    for p in players:
        pos = p.get("position", "MID")
        by_position.setdefault(pos, []).append(p)

    for pos, pos_players in by_position.items():
        ratings = [player_score(p) for p in pos_players]
        avg = np.mean(ratings) if ratings else 0
        std = np.std(ratings) if len(ratings) > 1 else 1

        for p, rating in zip(pos_players, ratings):
            imp = importance_score(rating, avg, std)
            db.execute(
                "UPDATE players SET importance_score=? WHERE name=? AND team_id=?",
                (round(imp, 3), p["name"], team_id),
            )
