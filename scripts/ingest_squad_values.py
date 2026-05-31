"""Ingest scraped squad values into the database."""

import sys
import pandas as pd
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.db.base import get_backend
from src.ingestion.fuzzy_matcher import FuzzyMatcher

CSV_PATH = Path("data/transfermarkt/squad_values_2025_26.csv")

def main():
    if not CSV_PATH.exists():
        print(f"[ERROR] CSV not found: {CSV_PATH}")
        return

    print("=" * 60)
    print("  Ingesting Squad Values (Season 2025-26)")
    print("=" * 60)

    db = get_backend()
    db.connect()
    fuzzy = FuzzyMatcher()

    # Load scraped data
    df = pd.read_csv(CSV_PATH)
    
    # Load DB teams
    db_teams = db.fetchall("SELECT id, name FROM teams")
    team_names = [t["name"] for t in db_teams]
    team_map = {t["name"]: t["id"] for t in db_teams}

    print(f"Loaded {len(df)} scraped records.")
    
    updated = 0
    not_found = []

    for _, row in df.iterrows():
        scraped_name = row["team_name"]
        match = fuzzy.match(scraped_name, team_names)
        
        if match:
            team_id = team_map[match]
            db.execute(
                """UPDATE teams 
                   SET squad_value = ?, avg_player_value = ?
                   WHERE id = ?""",
                (float(row["total_squad_value"]), float(row["avg_player_value"]), team_id)
            )
            updated += 1
        else:
            not_found.append(f"{scraped_name} ({row['league_code']})")

    db.close()

    print(f"\n[OK] Updated {updated} teams with squad values.")
    if not_found:
        print(f"[WARN] {len(not_found)} teams could not be matched:")
        for name in not_found[:10]:
            print(f"  - {name}")
        if len(not_found) > 10:
            print(f"  ... and {len(not_found)-10} more")

if __name__ == "__main__":
    main()
