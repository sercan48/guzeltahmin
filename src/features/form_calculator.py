"""Form calculator with momentum tracking and H2H analysis."""

import pandas as pd
import numpy as np

from config.constants import FORM_WINDOW
from src.features.team_strength import calculate_match_points, time_decay_weight


def form_momentum(team_matches: pd.DataFrame, team_id: int, n: int = FORM_WINDOW) -> float:
    """Calculate form momentum — is the team improving or declining?

    Returns:
        Positive = improving, negative = declining, 0 = stable
        Range roughly [-1, 1]
    """
    if len(team_matches) < 3:
        return 0.0

    recent = team_matches.tail(n)
    points = []

    for _, m in recent.iterrows():
        is_home = m["home_team_id"] == team_id
        pts = calculate_match_points(m["ft_result"], is_home)
        points.append(pts)

    if len(points) < 2:
        return 0.0

    # Linear regression slope of points over time
    x = np.arange(len(points))
    slope = np.polyfit(x, points, 1)[0]

    # Normalize to [-1, 1]
    return max(-1.0, min(1.0, slope))


def head_to_head(db, team1_id: int, team2_id: int, limit: int = 20, before_date: str = None) -> dict:
    """Get head-to-head statistics between two teams.

    Returns dict with:
        - total_matches
        - team1_wins, team2_wins, draws
        - team1_winrate
        - avg_goals
        - last_5_results
    """
    date_filter = "AND date < ?" if before_date else ""
    params = (team1_id, team2_id, team2_id, team1_id, before_date, limit) if before_date else (team1_id, team2_id, team2_id, team1_id, limit)

    matches = db.fetchall(
        f"""SELECT ft_home_goals, ft_away_goals, ft_result,
                  home_team_id, away_team_id, date
        FROM matches
        WHERE ((home_team_id=? AND away_team_id=?)
           OR (home_team_id=? AND away_team_id=?))
           AND ft_result IS NOT NULL
           {date_filter}
        ORDER BY date DESC
        LIMIT ?""",
        params,
    )

    if not matches:
        return {
            "total_matches": 0,
            "team1_wins": 0, "team2_wins": 0, "draws": 0,
            "team1_winrate": 0.5,
            "avg_goals": 2.5,
            "last_5_results": [],
        }

    t1_wins = t2_wins = draws = 0
    total_goals = 0

    for m in matches:
        if m["ft_home_goals"] is None or m["ft_away_goals"] is None:
            continue
        total_goals += m["ft_home_goals"] + m["ft_away_goals"]

        t1_is_home = m["home_team_id"] == team1_id
        if m["ft_result"] == "D":
            draws += 1
        elif (m["ft_result"] == "H" and t1_is_home) or (m["ft_result"] == "A" and not t1_is_home):
            t1_wins += 1
        else:
            t2_wins += 1

    total = len(matches)

    return {
        "total_matches": total,
        "team1_wins": t1_wins,
        "team2_wins": t2_wins,
        "draws": draws,
        "team1_winrate": t1_wins / total if total > 0 else 0.5,
        "avg_goals": total_goals / total if total > 0 else 2.5,
        "last_5_results": [
            "W" if (
                (m["ft_result"] == "H" and m["home_team_id"] == team1_id) or
                (m["ft_result"] == "A" and m["away_team_id"] == team1_id)
            ) else "L" if (
                (m["ft_result"] == "A" and m["home_team_id"] == team1_id) or
                (m["ft_result"] == "H" and m["away_team_id"] == team1_id)
            ) else "D"
            for m in matches[:5]
        ],
    }


def goals_stats(db, team_id: int, season: str, n: int = None, before_date: str = None) -> dict:
    """Calculate goal scoring and conceding stats for a team.

    Returns:
        avg_scored, avg_conceded (both home and away combined)
    """
    limit_clause = f"LIMIT {n}" if n else ""
    date_filter = "AND date < ?" if before_date else ""
    params_h = (team_id, season, before_date) if before_date else (team_id, season)
    params_a = (team_id, season, before_date) if before_date else (team_id, season)

    rows = db.fetchall(
        f"""SELECT ft_home_goals as scored, ft_away_goals as conceded
        FROM matches WHERE home_team_id=? AND season=? {date_filter}
        ORDER BY date DESC {limit_clause}""",
        params_h,
    )
    rows += db.fetchall(
        f"""SELECT ft_away_goals as scored, ft_home_goals as conceded
        FROM matches WHERE away_team_id=? AND season=? {date_filter}
        ORDER BY date DESC {limit_clause}""",
        params_a,
    )

    if not rows:
        return {"avg_scored": 1.2, "avg_conceded": 1.2}  # League average fallback

    scored = [r["scored"] for r in rows if r["scored"] is not None]
    conceded = [r["conceded"] for r in rows if r["conceded"] is not None]

    if not scored:
        return {"avg_scored": 1.2, "avg_conceded": 1.2}

    return {
        "avg_scored": sum(scored) / len(scored),
        "avg_conceded": sum(conceded) / len(conceded),
    }
