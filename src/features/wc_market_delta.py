"""
Calculates the 'Market Delta' for Sharp Money Sentiment.
Compares opening lines to closing lines.
"""

def calculate_market_delta(cursor, match_id: int):
    """
    Compares Opening Odds (Longest time to kickoff) to Closing Odds (Shortest time).
    Returns the percentage drop/drift for Home and Away.
    Positive Delta = Odds DROPPED (Sharp money backing).
    Negative Delta = Odds DRIFTED (Market fading).
    """
    # Fetch Opening Odds (Largest hours_to_kickoff)
    cursor.execute("SELECT odd_home, odd_away FROM wc_odds_movements WHERE match_id = ? ORDER BY hours_to_kickoff DESC LIMIT 1", (match_id,))
    open_odds = cursor.fetchone()
    
    # Fetch Closing/Current Odds (Smallest hours_to_kickoff)
    cursor.execute("SELECT odd_home, odd_away FROM wc_odds_movements WHERE match_id = ? ORDER BY hours_to_kickoff ASC LIMIT 1", (match_id,))
    close_odds = cursor.fetchone()
    
    if not open_odds or not close_odds:
        return {"home_delta": 0.0, "away_delta": 0.0}
        
    open_home, open_away = open_odds
    close_home, close_away = close_odds
    
    if open_home == 0 or open_away == 0:
        return {"home_delta": 0.0, "away_delta": 0.0}
        
    # Delta = (Opening - Closing) / Opening
    home_delta = (open_home - close_home) / float(open_home)
    away_delta = (open_away - close_away) / float(open_away)
    
    return {
        "home_delta": home_delta * 100.0,
        "away_delta": away_delta * 100.0
    }
