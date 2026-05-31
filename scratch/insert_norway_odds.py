import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.db.base import get_backend
from scripts.run_production_pipeline import run_production

# Odds map for the 6 matches
odds_data = {
    ("Aalesund", "HamKam"): {"home": 2.05, "draw": 3.70, "away": 3.20},
    ("Brann", "Sarpsborg 08"): {"home": 1.65, "draw": 4.33, "away": 4.33},
    ("Fredrikstad", "Start"): {"home": 1.78, "draw": 3.80, "away": 4.00},
    ("KFUM Oslo", "Tromso"): {"home": 2.85, "draw": 3.40, "away": 2.30},
    ("Rosenborg", "Bodo/Glimt"): {"home": 5.00, "draw": 4.50, "away": 1.55},
    ("Valerenga", "Kristiansund"): {"home": 1.53, "draw": 4.40, "away": 5.00}
}

db = get_backend()
db.connect()

try:
    print("=== Inserting Odds for Norway Eliteserien Fixtures ===")
    
    # Query today's Norway matches
    matches = db.fetchall("""
        SELECT m.id, t1.name as home, t2.name as away
        FROM matches m
        JOIN teams t1 ON m.home_team_id = t1.id
        JOIN teams t2 ON m.away_team_id = t2.id
        WHERE m.league_code = 'NORWAY_ELITESERIEN' AND m.date >= '2026-05-29' AND m.ft_result IS NULL
    """)
    
    inserted_count = 0
    for m in matches:
        home, away, match_id = m["home"], m["away"], m["id"]
        
        # Find odds in our map
        odds = None
        for (h, a), o in odds_data.items():
            if h.lower() in home.lower() and a.lower() in away.lower():
                odds = o
                break
                
        if not odds:
            print(f"  [WARN] No odds found in map for: {home} vs {away}")
            continue
            
        # Delete existing odds for this match first to prevent duplicate errors
        db.execute("DELETE FROM odds WHERE match_id = ?", (match_id,))
        
        # Insert Pinnacle odds
        db.execute(
            """INSERT INTO odds 
            (match_id, bookmaker, home_odds, draw_odds, away_odds, over25_odds, under25_odds)
            VALUES (?, 'Pinnacle', ?, ?, ?, 1.80, 2.00)""",
            (match_id, odds["home"], odds["draw"], odds["away"])
        )
        
        # Insert Bet365 odds
        db.execute(
            """INSERT INTO odds 
            (match_id, bookmaker, home_odds, draw_odds, away_odds, over25_odds, under25_odds)
            VALUES (?, 'Bet365', ?, ?, ?, 1.80, 2.00)""",
            (match_id, odds["home"], odds["draw"], odds["away"])
        )
        
        print(f"  [OK] Inserted odds for {home} vs {away} (Match ID: {match_id}) -> H: {odds['home']}, D: {odds['draw']}, A: {odds['away']}")
        inserted_count += 1
        
    print(f"\nSuccessfully inserted odds for {inserted_count} matches.")
    
finally:
    db.close()

# Trigger prediction pipeline
print("\n=== Running Production Prediction Pipeline ===")
run_production()
