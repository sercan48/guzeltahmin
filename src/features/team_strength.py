"""Team strength calculation with 40/60 weighting and time decay."""

import math
from datetime import datetime

import pandas as pd
import numpy as np

from config.constants import (
    SEASON_WEIGHT, FORM_WEIGHT, FORM_WINDOW,
    TIME_DECAY_LAMBDA, WIN_POINTS, DRAW_POINTS, LOSS_POINTS,
)


def calculate_match_points(result: str, is_home: bool) -> int:
    """Convert match result to points from a team's perspective."""
    if is_home:
        return {
            "H": WIN_POINTS, "D": DRAW_POINTS, "A": LOSS_POINTS
        }.get(result, 0)
    else:
        return {
            "A": WIN_POINTS, "D": DRAW_POINTS, "H": LOSS_POINTS
        }.get(result, 0)


def time_decay_weight(match_date: datetime, reference_date: datetime) -> float:
    """Calculate time decay weight for a match.

    More recent matches have higher weight.
    Half-life ~70 days (lambda=0.01).
    """
    days_ago = (reference_date - match_date).days
    if days_ago < 0:
        days_ago = 0
    return math.exp(-TIME_DECAY_LAMBDA * days_ago)


def season_average(team_matches: pd.DataFrame, team_id: int) -> float:
    """Calculate season average strength (0-1 normalized).

    Considers points per game, goals scored/conceded ratio.
    """
    if team_matches.empty:
        return 0.5  # Neutral default

    points = []
    goal_diff = []

    for _, m in team_matches.iterrows():
        is_home = m["home_team_id"] == team_id
        pts = calculate_match_points(m["ft_result"], is_home)
        points.append(pts)

        if is_home:
            goal_diff.append(m["ft_home_goals"] - m["ft_away_goals"])
        else:
            goal_diff.append(m["ft_away_goals"] - m["ft_home_goals"])

    ppg = sum(points) / len(points) if points else 0
    avg_gd = sum(goal_diff) / len(goal_diff) if goal_diff else 0

    # Normalize: PPG max=3, GD typically -3 to +3
    ppg_norm = ppg / 3.0
    gd_norm = (avg_gd + 3) / 6.0  # Map [-3, 3] → [0, 1]
    gd_norm = max(0, min(1, gd_norm))

    return ppg_norm * 0.7 + gd_norm * 0.3


def last_n_form(
    team_matches: pd.DataFrame,
    team_id: int,
    n: int = FORM_WINDOW,
    reference_date: datetime = None,
    league_standings: dict[int, int] = None,
) -> float:
    """Calculate form from last N matches with SOS normalization.

    Args:
        team_matches: All matches for the team, sorted by date
        team_id: Team ID to calculate form for
        n: Number of recent matches
        reference_date: Date to calculate from (for time decay)
        league_standings: Dict of team_id → league_position for SOS

    Returns:
        Normalized form score (0-1)
    """
    if team_matches.empty:
        return 0.5

    recent = team_matches.tail(n)
    if reference_date is None:
        reference_date = pd.Timestamp.now()

    weighted_points = []

    for _, m in recent.iterrows():
        is_home = m["home_team_id"] == team_id
        pts = calculate_match_points(m["ft_result"], is_home)

        # Time decay
        match_date = pd.to_datetime(m["date"])
        decay = time_decay_weight(match_date, reference_date)

        # SOS (Strength of Schedule) multiplier
        sos = 1.0
        if league_standings:
            opponent_id = m["away_team_id"] if is_home else m["home_team_id"]
            opponent_pos = league_standings.get(opponent_id, 10)
            total_teams = max(league_standings.values()) if league_standings else 20
            # Higher position = tougher opponent = higher SOS
            sos = 1.0 + (1.0 - opponent_pos / total_teams) * 0.3

        weighted_points.append(pts * decay * sos)

    if not weighted_points:
        return 0.5

    max_possible = WIN_POINTS * len(weighted_points) * 1.3  # Max with SOS bonus
    return sum(weighted_points) / max_possible


def team_strength(
    team_matches: pd.DataFrame,
    team_id: int,
    reference_date: datetime = None,
    league_standings: dict[int, int] = None,
) -> float:
    """Calculate combined team strength.

    Formula: (SeasonAvg × 0.40) + (Last5Form × 0.60)
    """
    s_avg = season_average(team_matches, team_id)
    l5_form = last_n_form(
        team_matches, team_id,
        reference_date=reference_date,
        league_standings=league_standings,
    )

    return (s_avg * SEASON_WEIGHT) + (l5_form * FORM_WEIGHT)


def home_advantage_factor(db, league_code: str, before_date: str = None) -> float:
    """Calculate home win percentage for a league.

    Returns a factor > 0.5 indicating how much home advantage matters.
    """
    date_filter = "AND date < ?" if before_date else ""
    params = (league_code, before_date) if before_date else (league_code,)
    
    result = db.fetchone(
        f"""SELECT
            COUNT(*) as total,
            SUM(CASE WHEN ft_result='H' THEN 1 ELSE 0 END) as home_wins
        FROM matches WHERE league_code=? {date_filter}""",
        params,
    )

    if not result or result["total"] == 0:
        return 0.46  # Global average home win rate

    return result["home_wins"] / result["total"]


def get_league_standings(db, league_code: str, season: str, before_date: str = None) -> dict[int, int]:
    """Get current league standings as {team_id: position}."""
    date_filter = "AND date < ?" if before_date else ""
    params = (league_code, season, before_date, league_code, season, before_date) if before_date else (league_code, season, league_code, season)
    
    rows = db.fetchall(
        f"""SELECT
            team_id,
            SUM(points) as total_points,
            SUM(gd) as total_gd
        FROM (
            SELECT home_team_id as team_id,
                CASE ft_result WHEN 'H' THEN 3 WHEN 'D' THEN 1 ELSE 0 END as points,
                (ft_home_goals - ft_away_goals) as gd
            FROM matches WHERE league_code=? AND season=? {date_filter}
            UNION ALL
            SELECT away_team_id as team_id,
                CASE ft_result WHEN 'A' THEN 3 WHEN 'D' THEN 1 ELSE 0 END as points,
                (ft_away_goals - ft_home_goals) as gd
            FROM matches WHERE league_code=? AND season=? {date_filter}
        ) GROUP BY team_id
        ORDER BY total_points DESC, total_gd DESC""",
        params,
    )

    return {row["team_id"]: i + 1 for i, row in enumerate(rows)}
