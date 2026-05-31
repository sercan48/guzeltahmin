"""Match Context Engine.

Handles non-standard contextual features like:
- Derby / Rivalry Flag (is_derby)
- Referee vs Aggression mapping (red_card_risk)
"""

DERBY_MATCHUPS = [
    # Turkey (Süper Lig)
    {"Galatasaray", "Fenerbahçe"},
    {"Galatasaray", "Beşiktaş"},
    {"Fenerbahçe", "Beşiktaş"},
    {"Trabzonspor", "Fenerbahçe"},
    # England
    {"Manchester United", "Manchester City"},
    {"Liverpool", "Everton"},
    {"Arsenal", "Tottenham Hotspur"},
    {"Manchester United", "Liverpool"},
    {"Arsenal", "Chelsea"},
    # Spain
    {"Real Madrid", "Barcelona"},
    {"Real Madrid", "Atletico Madrid"},
    {"Real Betis", "Sevilla"},
    # Italy
    {"Inter Milan", "AC Milan"},
    {"Juventus", "Inter Milan"},
    {"AS Roma", "Lazio"},
    # Germany
    {"Borussia Dortmund", "Schalke 04"},
    {"Bayern München", "Borussia Dortmund"},
    # France
    {"Paris Saint-Germain", "Marseille"},
    {"Lyon", "Saint-Etienne"},
    # Scotland
    {"Celtic", "Rangers"},
    # Portugal
    {"Benfica", "Sporting CP"},
    {"Benfica", "FC Porto"},
    {"FC Porto", "Sporting CP"},
    # Netherlands
    {"Ajax", "Feyenoord"},
    {"Ajax", "PSV Eindhoven"},
]


def check_is_derby(home_team_name: str, away_team_name: str) -> int:
    """Returns 1 if the match is a recognized fierce rivalry/derby, else 0."""
    matchup = {home_team_name, away_team_name}
    for derby in DERBY_MATCHUPS:
        if matchup == derby:
            return 1
    return 0


def calculate_red_card_risk(home_aggression: float, away_aggression: float, referee_strictness: float) -> float:
    """Calculate risk of red cards/disruption based on team aggression and ref strictness.
    
    Args:
        home_aggression: Average defending/physical stat or specific aggression metric (0-100)
        away_aggression: Average defending/physical stat or specific aggression metric (0-100)
        referee_strictness: 0.0 to 1.0 (where 1.0 is extremely strict)
        
    Returns:
        Risk factor 0.0 to 10.0
    """
    # Base risk is driven by strict referees
    ref_factor = referee_strictness * 5.0
    
    # High aggression teams against a strict ref multiply the risk
    # Let's say normal aggression is around 60.
    home_agg_factor = max(0, home_aggression - 50) / 50.0  # 0.0 to 1.0
    away_agg_factor = max(0, away_aggression - 50) / 50.0  # 0.0 to 1.0
    
    combined_agg_factor = (home_agg_factor + away_agg_factor) * 2.5
    
    risk = ref_factor + combined_agg_factor
    
    return round(min(10.0, max(0.0, risk)), 2)

