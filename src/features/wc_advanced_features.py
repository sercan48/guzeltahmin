"""
Advanced Feature Engineering for World Cup Monte Carlo Engine.
Calculates Positional Deltas, Synergy, and Fatigue.
"""
from collections import Counter

class Player:
    def __init__(self, name, position, club_name, caps, rating):
        self.name = name
        self.position = position # 'GK', 'DEF', 'MID', 'ATT'
        self.club_name = club_name
        self.caps = caps
        self.rating = rating

def avg_rating(lineup, position):
    players = [p for p in lineup if p.position == position]
    if not players:
        return 70.0 # Default fallback
    return sum(p.rating for p in players) / len(players)

def calculate_positional_dynamics(lineup_a, lineup_b):
    """Calculates Attack vs Defense Deltas and Midfield Dominance."""
    a_att = avg_rating(lineup_a, 'ATT')
    a_mid = avg_rating(lineup_a, 'MID')
    a_def = avg_rating(lineup_a, 'DEF')
    
    b_att = avg_rating(lineup_b, 'ATT')
    b_mid = avg_rating(lineup_b, 'MID')
    b_def = avg_rating(lineup_b, 'DEF')
    
    # Deltas
    a_att_vs_b_def = a_att - b_def
    b_att_vs_a_def = b_att - a_def
    
    # Midfield Dominance
    a_midfield_ratio = a_mid / max(b_mid, 1)
    b_midfield_ratio = b_mid / max(a_mid, 1)
    
    # Weakest Link Penalty (Players >15% below team avg)
    a_team_avg = sum(p.rating for p in lineup_a) / max(len(lineup_a), 1)
    b_team_avg = sum(p.rating for p in lineup_b) / max(len(lineup_b), 1)
    
    a_penalty = sum(1 for p in lineup_a if p.rating < (a_team_avg * 0.85))
    b_penalty = sum(1 for p in lineup_b if p.rating < (b_team_avg * 0.85))
    
    return {
        "a_att_vs_b_def": a_att_vs_b_def,
        "b_att_vs_a_def": b_att_vs_a_def,
        "a_midfield_dominance": a_midfield_ratio,
        "b_midfield_dominance": b_midfield_ratio,
        "a_weakest_links": a_penalty,
        "b_weakest_links": b_penalty
    }

def calculate_chemistry(lineup):
    """Calculates Club Synergy and Average International Caps."""
    if not lineup:
        return 0, 0
    clubs = [p.club_name for p in lineup]
    # Bonus for pairs/trios playing in the same club
    synergy_bonus = sum(count for count in Counter(clubs).values() if count > 1) * 2.5
    avg_caps = sum(p.caps for p in lineup) / len(lineup)
    return synergy_bonus, avg_caps

def calculate_fatigue_and_env(distance_km, altitude_meters, continent):
    """Calculates travel fatigue and environmental impact."""
    # 1.5% penalty per 1000km traveled
    fatigue_penalty = (distance_km / 1000.0) * 1.5 
    
    env_modifier = 0
    if altitude_meters > 1500 and continent == "Europe":
        env_modifier -= 5.0 # Altitude penalty for non-adapted teams
        
    return fatigue_penalty, env_modifier
