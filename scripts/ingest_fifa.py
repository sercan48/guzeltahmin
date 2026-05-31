"""Ingest FIFA player data into the database.

Matches Kaggle FIFA datasets to teams in the SQLite database and populates
the `players` table with stats, resolving fuzzy team names automatically.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.db.base import get_backend
from src.ingestion.fifa_loader import prepare_fifa_data
from src.ingestion.fuzzy_matcher import FuzzyMatcher


def ingest_fifa_players(db, df, fuzzy: FuzzyMatcher = None):
    """Ingest processed FIFA dataset into DB."""
    if df.empty:
        return 0

    if fuzzy is None:
        fuzzy = FuzzyMatcher()

    inserted = 0
    updated = 0
    unmatched_clubs = set()

    # Pre-fetch all teams to optimize fuzzy matching
    all_teams = db.fetchall("SELECT id, name, league_code FROM teams")
    team_names = [t["name"] for t in all_teams]
    team_map = {t["name"]: t["id"] for t in all_teams}

    for _, row in df.iterrows():
        name = str(row.get("name", "")).strip()
        club = str(row.get("club", "")).strip()

        if not name or not club or club == "nan":
            continue

        # Find team ID using fuzzy match
        matched_club = fuzzy.match(club, team_names)
        if not matched_club:
            unmatched_clubs.add(club)
            continue

        team_id = team_map[matched_club]

        pos = row.get("position", "MID")
        overall = int(row.get("fifa_overall", 0))
        pace = int(row.get("fifa_pace", 0))
        shooting = int(row.get("fifa_shooting", 0))
        passing = int(row.get("fifa_passing", 0))
        dribbling = int(row.get("fifa_dribbling", 0))
        defending = int(row.get("fifa_defending", 0))
        physical = int(row.get("fifa_physical", 0))
        market_val = float(row.get("market_value", 0.0))

        # Check if player exists
        existing = db.fetchone(
            "SELECT id FROM players WHERE name=? AND team_id=?", 
            (name, team_id)
        )

        if existing:
            # Update existing player stats
            db.execute(
                """UPDATE players SET
                position=?, fifa_overall=?, fifa_pace=?, fifa_shooting=?,
                fifa_passing=?, fifa_dribbling=?, fifa_defending=?, fifa_physical=?,
                market_value=?
                WHERE id=?""",
                (pos, overall, pace, shooting, passing, dribbling, 
                 defending, physical, market_val, existing["id"])
            )
            updated += 1
        else:
            # Insert new player
            db.execute(
                """INSERT INTO players (
                    name, team_id, position, fifa_overall, fifa_pace, 
                    fifa_shooting, fifa_passing, fifa_dribbling, fifa_defending, 
                    fifa_physical, market_value, importance_score
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0.0)""",
                (name, team_id, pos, overall, pace, shooting, passing, 
                 dribbling, defending, physical, market_val)
            )
            inserted += 1

    if unmatched_clubs:
        print(f"\n[WARN] Could not match {len(unmatched_clubs)} global clubs. "
              f"(This is normal for clubs outside our 11 leagues).")
        
    return inserted, updated


def main():
    print("=" * 60)
    print("  FIFA -> Database Ingestion")
    print("=" * 60)

    db = get_backend()
    db.connect()

    try:
        before = db.fetchone("SELECT COUNT(*) as cnt FROM players")
        print(f"  Existing players in DB: {before['cnt']}\n")

        # Load and parse Kaggle CSV
        print("[1/2] Loading FIFA dataset from data/fifa/...")
        df = prepare_fifa_data()

        if df.empty:
            print("[ERROR] No data loaded. Exiting.")
            return

        print(f"\n[2/2] Ingesting {len(df)} players into DB (using FuzzyMatch)...")
        fuzzy = FuzzyMatcher()
        inserted, updated = ingest_fifa_players(db, df, fuzzy)

        after = db.fetchone("SELECT COUNT(*) as cnt FROM players")
        print(f"\n[OK] Ingstion Complete!")
        print(f"  - New specific players found & inserted: {inserted}")
        print(f"  - Players updated: {updated}")
        print(f"  - Total players in DB now: {after['cnt']}")

    finally:
        db.close()


if __name__ == "__main__":
    main()
