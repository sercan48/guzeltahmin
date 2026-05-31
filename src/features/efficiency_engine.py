"""Efficiency engine: market value vs actual performance correlation."""

import numpy as np


def efficiency_score(market_value: float, performance_rating: float) -> float:
    """Calculate how efficiently a team/player performs relative to cost.

    A team with low market value but high performance = very efficient.
    A team with high market value but low performance = inefficient.

    Returns:
        Efficiency ratio (>1 = overperforming, <1 = underperforming)
    """
    if market_value <= 0:
        return 1.0  # Can't calculate without value data

    # Normalize performance to same scale as value
    expected_performance = market_value_to_expected(market_value)

    if expected_performance <= 0:
        return 1.0

    return performance_rating / expected_performance


def market_value_to_expected(value_millions: float) -> float:
    """Map market value to expected performance using log relationship.

    The relationship between spending and performance is logarithmic —
    doubling the squad value doesn't double performance.
    """
    if value_millions <= 0:
        return 50.0
    # Log scale: €10M → ~50, €100M → ~66, €1B → ~80
    return 50 + 10 * np.log10(max(value_millions, 1))


def team_efficiency(
    squad_value: float,
    points_per_game: float,
    goals_scored_avg: float,
    goals_conceded_avg: float,
) -> dict:
    """Calculate comprehensive team efficiency metrics.

    Returns dict with:
        - value_efficiency: PPG relative to squad cost
        - attack_efficiency: Goals scored relative to cost
        - defense_efficiency: Goals conceded relative to cost (inverted)
        - overall: Combined efficiency score
    """
    expected = market_value_to_expected(squad_value)

    ppg_norm = (points_per_game / 3.0) * 100  # Normalize to 0-100
    attack_norm = min(goals_scored_avg * 33, 100)  # 3 goals/game = 100
    defense_norm = max(100 - goals_conceded_avg * 33, 0)  # 0 conceded = 100

    value_eff = ppg_norm / expected if expected > 0 else 1.0
    attack_eff = attack_norm / expected if expected > 0 else 1.0
    defense_eff = defense_norm / expected if expected > 0 else 1.0

    return {
        "value_efficiency": round(value_eff, 3),
        "attack_efficiency": round(attack_eff, 3),
        "defense_efficiency": round(defense_eff, 3),
        "overall": round((value_eff * 0.5 + attack_eff * 0.25 + defense_eff * 0.25), 3),
    }


def simulated_xg_efficiency(
    scored_avg: float, 
    attack_rating: float, 
    league_avg_goals: float = 1.35,
    db=None,
    team_id: int = None,
    season: str = None,
) -> float:
    """Calculate an xG proxy.
    
    If Transfermarkt goals_per_90 data is available in DB, use that as
    the ground truth expected rate instead of the FIFA-based estimate.
    """
    # Try real data first
    if db and team_id and season:
        try:
            row = db.fetchone(
                "SELECT goals_per_90 FROM team_season_stats WHERE team_id=? AND season=?",
                (team_id, season)
            )
            if row and row["goals_per_90"] > 0:
                real_expected = row["goals_per_90"] * 15  # ~15 players contribute
                if real_expected > 0:
                    return scored_avg / real_expected
        except Exception:
            pass
    
    # Fallback: FIFA-based estimate
    if attack_rating <= 0:
        return 1.0
        
    rating_diff = attack_rating - 75.0
    expected_scored = league_avg_goals * (1.0 + (rating_diff / 5.0) * 0.15)
    
    if expected_scored <= 0:
        return 1.0
        
    return scored_avg / expected_scored
