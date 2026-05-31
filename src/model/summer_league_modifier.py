"""
Summer League Hybrid Modifier.
Applies specific rules to base probabilities for Summer Leagues (MLS, Brazil, Norway, Sweden, Japan)
to avoid corrupting the core European ML weights.
"""
import logging

logger = logging.getLogger(__name__)

# Constants for Supported Summer Leagues
MLS = "USA_MLS"
BRAZIL_SERIE_A = "BRAZIL_SERIE_A"
NORWAY_ELITESERIEN = "NORWAY_ELITESERIEN"
SWEDEN_ALLSVENSKAN = "SWEDEN_ALLSVENSKAN"
JAPAN_J1 = "JAPAN_J1"

SUMMER_LEAGUES = [MLS, BRAZIL_SERIE_A, NORWAY_ELITESERIEN, SWEDEN_ALLSVENSKAN, JAPAN_J1]

def normalize_probabilities(prob_dict):
    """Ensure probabilities sum to exactly 100."""
    total = sum(prob_dict.values())
    if total == 0:
        return prob_dict
    return {k: (v / total) * 100 for k, v in prob_dict.items()}

def apply_summer_modifiers(base_prob: dict, match_data: dict, league_id: str):
    """
    Adjusts the core prediction probability based on localized physical factors.
    base_prob: {'home_win': 45.0, 'draw': 25.0, 'away_win': 30.0}
    match_data: dict containing pitch_type, travel_distance_km, cup_rotation_fatigue, etc.
    """
    if league_id not in SUMMER_LEAGUES:
        # Pass-through for European matches
        return base_prob
        
    prob = base_prob.copy()
    
    # 1. SCANDINAVIA: Artificial Turf Advantage
    if league_id in [NORWAY_ELITESERIEN, SWEDEN_ALLSVENSKAN]:
        if match_data.get('pitch_type') == 'ARTIFICIAL':
            logger.info(f"Applying Artificial Turf modifier for {league_id}")
            prob['home_win'] *= 1.15  # 15% boost to home team on plastic pitch
            
    # 2. AMERICAS: Severe Travel & Altitude Fatigue
    elif league_id == BRAZIL_SERIE_A:
        distance = match_data.get('travel_distance_km', 0)
        if distance > 2000:
            logger.info(f"Applying severe travel fatigue modifier for {league_id} ({distance}km)")
            prob['away_win'] *= 0.85  # Drop away win prob by 15%
            
        if match_data.get('cup_rotation_fatigue', False):
            logger.info("Applying Copa Libertadores/Sudamericana fatigue penalty.")
            prob['home_win'] *= 0.90 # Mild drop if they played mid-week
            
    # 3. MLS: Designated Player (DP) Impact
    elif league_id == MLS:
        # Example: difference in DP ratio (home - away)
        dp_diff = match_data.get('home_dp_ratio', 0) - match_data.get('away_dp_ratio', 0)
        if dp_diff != 0:
            logger.info(f"Applying MLS DP Ratio Modifier (Delta: {dp_diff})")
            # +5% prob for every 1.0 difference in DP ratio
            prob['home_win'] += (dp_diff * 5.0) 
            prob['away_win'] -= (dp_diff * 5.0)
            
    # 4. JAPAN: Extreme Weather / Typhoons
    elif league_id == JAPAN_J1:
        weather = match_data.get('weather_condition', 'NORMAL')
        if weather == 'EXTREME_HUMIDITY':
            logger.info(f"Applying J1 Extreme Humidity modifier (Favors draw/low scoring)")
            prob['draw'] *= 1.20
            
    # Normalize probabilities back to 100% scale
    # Since we added/multiplied, we must re-normalize
    # Ensure no negative probabilities
    for key in prob:
        if prob[key] < 1.0:
            prob[key] = 1.0
            
    return normalize_probabilities(prob)
