import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.db.base import get_backend
from src.ingestion.fuzzy_matcher import FuzzyMatcher
from scripts.run_production_pipeline import run_production

# Brazil Serie A fixtures for May 30-31, 2026
fixtures_data = [
    {
        "home": "Athletico-PR", "away": "Mirassol", "date": "2026-05-30",
        "odds": {"home": 1.65, "draw": 3.60, "away": 5.00}
    },
    {
        "home": "Flamengo RJ", "away": "Coritiba", "date": "2026-05-30",
        "odds": {"home": 1.45, "draw": 4.20, "away": 6.50}
    },
    {
        "home": "Gremio", "away": "Corinthians", "date": "2026-05-30",
        "odds": {"home": 1.95, "draw": 3.30, "away": 3.80}
    },
    {
        "home": "Bahia", "away": "Botafogo RJ", "date": "2026-05-30",
        "odds": {"home": 2.10, "draw": 3.30, "away": 3.30}
    },
    {
        "home": "Santos", "away": "Vitoria", "date": "2026-05-30",
        "odds": {"home": 1.75, "draw": 3.50, "away": 4.50}
    },
    {
        "home": "Bragantino", "away": "Internacional", "date": "2026-05-31",
        "odds": {"home": 2.30, "draw": 3.20, "away": 3.00}
    },
    {
        "home": "Vasco", "away": "Atletico-MG", "date": "2026-05-31",
        "odds": {"home": 2.70, "draw": 3.20, "away": 2.50}
    },
    {
        "home": "Palmeiras", "away": "Chapecoense-SC", "date": "2026-05-31",
        "odds": {"home": 1.40, "draw": 4.50, "away": 7.50}
    },
    {
        "home": "Cruzeiro", "away": "Fluminense", "date": "2026-05-31",
        "odds": {"home": 2.05, "draw": 3.30, "away": 3.40}
    },
    {
        "home": "Remo", "away": "Sao Paulo", "date": "2026-05-31",
        "odds": {"home": 3.50, "draw": 3.30, "away": 2.05}
    }
]

db = get_backend()
db.connect()

try:
    print("=== Inserting Brazil Serie A Fixtures and Odds ===")
    
    # Get all teams in database
    db_teams = db.fetchall("SELECT id, name FROM teams")
    team_names = [t["name"] for t in db_teams]
    team_map = {t["name"]: t["id"] for t in db_teams}
    
    fuzzy = FuzzyMatcher()
    
    inserted_matches = 0
    inserted_odds = 0
    
    for fix in fixtures_data:
        home_match = fuzzy.match(fix["home"], team_names)
        away_match = fuzzy.match(fix["away"], team_names)
        
        if not home_match or not away_match:
            print(f"  [ERROR] Could not match team names: {fix['home']} vs {fix['away']}")
            continue
            
        home_id = team_map[home_match]
        away_id = team_map[away_match]
        date_str = f"{fix['date']} 00:00:00"
        
        # Check if already exists in DB
        existing = db.fetchone(
            "SELECT id FROM matches WHERE date=? AND home_team_id=? AND away_team_id=?",
            (date_str, home_id, away_id)
        )
        
        if existing:
            match_id = existing["id"]
            print(f"  [SKIP] Match already exists: {home_match} vs {away_match} ({fix['date']}) (ID: {match_id})")
        else:
            # Insert match
            cursor = db.execute(
                """INSERT INTO matches 
                (date, home_team_id, away_team_id, league_code, season, ft_result)
                VALUES (?, ?, ?, 'BRAZIL_SERIE_A', '2026', NULL)""",
                (date_str, home_id, away_id)
            )
            match_id = cursor.lastrowid
            print(f"  [OK] Inserted Match: {home_match} vs {away_match} ({fix['date']}) -> Match ID: {match_id}")
            inserted_matches += 1
            
        # Delete existing odds
        db.execute("DELETE FROM odds WHERE match_id = ?", (match_id,))
        
        # Insert Pinnacle odds
        odds = fix["odds"]
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
        
        inserted_odds += 1
        
    print(f"\nSuccessfully inserted {inserted_matches} matches and {inserted_odds} odds groups.")
    
finally:
    db.close()

# Trigger predictions
print("\n=== Running Production Prediction Pipeline ===")
run_production()
