"""xG-based feature engineering — time-decay weighted form calculator.

Applies exponential decay to the last N matches so recent offensive/defensive
trends carry more weight.  Decay formula:  w_i = exp(-λ * i)  where i=0 is
the most recent match and λ controls how fast old data fades.

Default λ=0.25 gives approximate weights:
  match 0 (latest): 1.00
  match 1:          0.78
  match 2:          0.61
  match 3:          0.47
  match 4:          0.29
"""

import math
import logging
from typing import Optional

logger = logging.getLogger(__name__)

# Decay rate: higher = more aggressive recency bias
XG_DECAY_LAMBDA = 0.25
XG_WINDOW = 5


def _time_decay_weights(n: int, decay: float = XG_DECAY_LAMBDA) -> list[float]:
    """Generate normalized exponential decay weights for n items (index 0 = most recent)."""
    raw = [math.exp(-decay * i) for i in range(n)]
    total = sum(raw)
    return [w / total for w in raw]


def compute_xg_features(db, team_id: int, league_code: str, is_home: bool,
                         n_matches: int = XG_WINDOW, before_date: Optional[str] = None) -> dict:
    """Compute time-decay weighted xG features for a team.

    Returns dict with keys:
      {side}_xg_avg              – decay-weighted xG per match
      {side}_xg_overperformance  – decay-weighted (goals - xG) delta
      {side}_xga_avg             – decay-weighted xG conceded per match
    """
    side = "home" if is_home else "away"
    team_col = "home_team_id" if is_home else "away_team_id"
    xg_col = f"{'home' if is_home else 'away'}_xg"
    xga_col = f"{'away' if is_home else 'home'}_xg"  # xG Against
    goals_col = f"ft_{'home' if is_home else 'away'}_goals"
    conceded_col = f"ft_{'away' if is_home else 'home'}_goals"

    # Try match_xg table first (Understat data)
    query_xg = f"""
        SELECT mx.{xg_col.replace('ft_', '')} as xg,
               mx.{xga_col.replace('ft_', '')} as xga,
               m.{goals_col} as goals,
               m.{conceded_col} as conceded,
               m.date
        FROM match_xg mx
        JOIN matches m ON m.id = mx.match_id
        WHERE m.{team_col} = ? AND m.league_code = ?
    """
    params_xg = [team_id, league_code]
    if before_date:
        query_xg += " AND m.date < ? "
        params_xg.append(before_date)
    query_xg += " ORDER BY m.date DESC LIMIT ? "
    params_xg.append(n_matches)

    rows = db.fetchall(query_xg, tuple(params_xg))

    if not rows or len(rows) < 2:
        # Fallback: matches table xG columns
        query_fb1 = f"""
            SELECT {xg_col} as xg, {goals_col} as goals,
                   {conceded_col} as conceded, date
            FROM matches
            WHERE {team_col} = ? AND league_code = ? AND {xg_col} IS NOT NULL
        """
        params_fb1 = [team_id, league_code]
        if before_date:
            query_fb1 += " AND date < ? "
            params_fb1.append(before_date)
        query_fb1 += " ORDER BY date DESC LIMIT ? "
        params_fb1.append(n_matches)
        rows = db.fetchall(query_fb1, tuple(params_fb1))

    if not rows or len(rows) < 2:
        # Final fallback: estimate from goals only
        query_fb2 = f"""
            SELECT {goals_col} as goals, {conceded_col} as conceded
            FROM matches
            WHERE {team_col} = ? AND league_code = ?
                  AND {goals_col} IS NOT NULL
        """
        params_fb2 = [team_id, league_code]
        if before_date:
            query_fb2 += " AND date < ? "
            params_fb2.append(before_date)
        query_fb2 += " ORDER BY date DESC LIMIT ? "
        params_fb2.append(n_matches)
        rows = db.fetchall(query_fb2, tuple(params_fb2))

        if rows:
            weights = _time_decay_weights(len(rows))
            w_goals = sum(w * (r["goals"] or 0) for w, r in zip(weights, rows))
            w_conceded = sum(w * (r["conceded"] or 0) for w, r in zip(weights, rows))
            return {
                f"{side}_xg_avg": round(w_goals, 3),
                f"{side}_xg_overperformance": 0.0,
                f"{side}_xga_avg": round(w_conceded, 3),
            }
        return {
            f"{side}_xg_avg": 1.30,  # Sensible default to prevent draw-spam skew when history is missing
            f"{side}_xg_overperformance": 0.0,
            f"{side}_xga_avg": 1.30,
        }

    weights = _time_decay_weights(len(rows))

    xg_vals = [(r.get("xg") or 0) for r in rows]
    goal_vals = [(r.get("goals") or 0) for r in rows]
    conceded_vals = [(r.get("conceded") or 0) for r in rows]
    xga_vals = [(r.get("xga") or r.get("conceded") or 0) for r in rows]

    w_xg = sum(w * v for w, v in zip(weights, xg_vals))
    w_goals = sum(w * v for w, v in zip(weights, goal_vals))
    w_xga = sum(w * v for w, v in zip(weights, xga_vals))
    overperf = w_goals - w_xg

    return {
        f"{side}_xg_avg": round(w_xg, 3),
        f"{side}_xg_overperformance": round(overperf, 3),
        f"{side}_xga_avg": round(w_xga, 3),
    }


def compute_match_xg_features(db, home_team_id: int, away_team_id: int,
                                league_code: str, before_date: Optional[str] = None) -> dict:
    """Compute all xG features for a match.

    Returns 6 features: home_xg_avg, home_xg_overperformance, home_xga_avg,
                        away_xg_avg, away_xg_overperformance, away_xga_avg.
    """
    home = compute_xg_features(db, home_team_id, league_code, is_home=True, before_date=before_date)
    away = compute_xg_features(db, away_team_id, league_code, is_home=False, before_date=before_date)
    return {**home, **away}
