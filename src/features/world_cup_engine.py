"""
World Cup 2026 Prediction Engine - Power Score Calculator (SQLite Native)
Calculates a team's true strength based on Elo, EA FC ratings, and environmental factors.
"""
import sqlite3
from pathlib import Path

def normalize_elo(elo: int) -> float:
    base = min(max(elo, 1500), 2200)
    return ((base - 1500) / 700.0) * 100

def normalize_squad_score(score: float) -> float:
    return min(max(score, 0), 100)

def normalize_form(points: float) -> float:
    return min((points / 30.0) * 100, 100)

def get_elo_rating(cursor, team_id: int) -> int:
    cursor.execute("SELECT elo_rating FROM teams WHERE id = ?", (team_id,))
    row = cursor.fetchone()
    return row[0] if row else 1500

def get_confirmed_lineup(cursor, match_id: int, team_id: int) -> dict:
    cursor.execute("SELECT avg_ea_fc_rating, star_player_count FROM match_lineups WHERE match_id = ? AND team_id = ?", (match_id, team_id))
    row = cursor.fetchone()
    if row:
        return {"avg_ea_fc_rating": row[0] or 70.0, "star_player_count": row[1] or 0}
    return {"avg_ea_fc_rating": 70.0, "star_player_count": 0}

def get_last_10_matches(cursor, team_id: int) -> list:
    """
    Returns the last 10 match results for form calculation.

    Primary path: queries match_results table.
    Fallback: deterministic synthetic history from wc_intelligence_engine
    (different per team_id, eliminating the hardcoded 3-match identical mock).
    """
    try:
        cursor.execute(
            "SELECT match_type, result_points FROM match_results "
            "WHERE team_id = ? ORDER BY match_date DESC LIMIT 10",
            (team_id,),
        )
        rows = cursor.fetchall()
        if rows:
            return [{"type": r[0], "result_points": r[1]} for r in rows]
    except Exception:
        pass

    from src.model.wc_intelligence_engine import synthetic_form_history
    return synthetic_form_history(team_id)

def get_venue(cursor, venue_id: int) -> dict:
    if not venue_id:
        return {"altitude_meters": 0, "climate_type": "Neutral"}
    cursor.execute("SELECT altitude_meters, climate_type FROM venues WHERE id = ?", (venue_id,))
    row = cursor.fetchone()
    if row:
        return {"altitude_meters": row[0], "climate_type": row[1]}
    return {"altitude_meters": 0, "climate_type": "Neutral"}

def get_team_continent(cursor, team_id: int) -> str:
    cursor.execute("SELECT continent FROM teams WHERE id = ?", (team_id,))
    row = cursor.fetchone()
    return row[0] if row else "Unknown"

def calculate_team_power_score(cursor, team_id: int, match_id: int, opponent_id: int, venue_id: int) -> float:
    # Weights
    W_ELO = 0.40
    W_SQUAD = 0.35
    W_FORM = 0.15
    W_ENV = 0.10

    elo_rating = get_elo_rating(cursor, team_id) 
    elo_score = normalize_elo(elo_rating)

    lineup = get_confirmed_lineup(cursor, match_id, team_id)
    squad_score = (lineup["avg_ea_fc_rating"] * 0.7) + (lineup["star_player_count"] * 3)
    squad_score = normalize_squad_score(squad_score)

    recent_matches = get_last_10_matches(cursor, team_id)
    form_score = 0
    for match in recent_matches:
        weight = 1.0 if match["type"] in ['WCQ', 'Euro', 'Copa'] else 0.1
        form_score += match["result_points"] * weight
    form_score = normalize_form(form_score)

    venue = get_venue(cursor, venue_id)
    team_continent = get_team_continent(cursor, team_id)
    env_score = 50 
    
    if venue["altitude_meters"] > 1500:
        if team_continent == 'South America':
            env_score += 20
        elif team_continent == 'Europe':
            env_score -= 15
            
    if venue["climate_type"] == 'Humid Subtropical' and team_continent == 'Europe':
        env_score -= 10

    final_score = (
        (elo_score * W_ELO) +
        (squad_score * W_SQUAD) +
        (form_score * W_FORM) +
        (env_score * W_ENV)
    )
    return final_score

def match_probability(cursor, match_id: int, home_team_id: int, away_team_id: int, venue_id: int):
    home_power = calculate_team_power_score(cursor, home_team_id, match_id, away_team_id, venue_id)
    away_power = calculate_team_power_score(cursor, away_team_id, match_id, home_team_id, venue_id)
    
    total = home_power + away_power
    if total == 0:
        return {"home": 0.33, "draw": 0.34, "away": 0.33}
        
    diff = home_power - away_power
    home_prob = (home_power / total) * 100
    away_prob = (away_power / total) * 100
    
    draw_prob = 0
    if abs(diff) < 10:
        draw_prob = 30 - abs(diff) 
        home_prob -= draw_prob / 2
        away_prob -= draw_prob / 2

    return {
        "home_power": round(home_power, 2),
        "away_power": round(away_power, 2),
        "home_win_prob": round(home_prob, 2),
        "draw_prob": round(draw_prob, 2),
        "away_win_prob": round(away_prob, 2)
    }

if __name__ == "__main__":
    db_path = Path("data/guzel_tahmin.db")
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    print("Testing Local World Cup Engine...")
    probs = match_probability(cur, 1, 1001, 1002, 5)
    print(probs)
    conn.close()
