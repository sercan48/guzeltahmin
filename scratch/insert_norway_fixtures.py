import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.db.base import get_backend
from src.ingestion.fuzzy_matcher import FuzzyMatcher
from scripts.run_production_pipeline import run_production

# Upcoming matches on Friday, May 29, 2026
upcoming_fixtures = [
    {"home": "Aalesund", "away": "HamKam", "date": "2026-05-29"},
    {"home": "Brann", "away": "Sarpsborg 08", "date": "2026-05-29"},
    {"home": "Fredrikstad", "away": "Start", "date": "2026-05-29"},
    {"home": "KFUM Oslo", "away": "Tromso", "date": "2026-05-29"},
    {"home": "Rosenborg", "away": "Bodo/Glimt", "date": "2026-05-29"},
    {"home": "Valerenga", "away": "Kristiansund", "date": "2026-05-29"}
]

db = get_backend()
db.connect()

try:
    # Get all teams in database
    db_teams = db.fetchall("SELECT id, name FROM teams")
    team_names = [t["name"] for t in db_teams]
    team_map = {t["name"]: t["id"] for t in db_teams}
    
    fuzzy = FuzzyMatcher()
    
    print("=== Inserting Upcoming Norway Eliteserien Fixtures ===")
    inserted_count = 0
    
    for fix in upcoming_fixtures:
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
            print(f"  [SKIP] Match already exists: {home_match} vs {away_match} ({fix['date']})")
            continue
            
        # Insert match
        db.execute(
            """INSERT INTO matches 
            (date, home_team_id, away_team_id, league_code, season, ft_result)
            VALUES (?, ?, ?, 'NORWAY_ELITESERIEN', '2026', NULL)""",
            (date_str, home_id, away_id)
        )
        print(f"  [OK] Inserted: {home_match} (ID: {home_id}) vs {away_match} (ID: {away_id}) for {fix['date']}")
        inserted_count += 1
        
    print(f"\nSuccessfully inserted {inserted_count} upcoming Norway fixtures.")
    
finally:
    db.close()

# Now run production predictions
print("\n=== Running Production Prediction Pipeline ===")
run_production()
