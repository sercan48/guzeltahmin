"""Ingest openfootball JSON data into the database.

Merges with existing Football-Data.co.uk data — avoids duplicates
and adds new historical matches (2010-2019) that Football-Data doesn't cover.
"""

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.db.base import get_backend
from src.ingestion.openfootball_loader import load_all_openfootball
from src.ingestion.fuzzy_matcher import FuzzyMatcher


def ingest_openfootball(db, df, fuzzy: FuzzyMatcher = None):
    """Ingest openfootball matches into database, deduplicating against existing data.

    Returns count of NEW matches inserted.
    """
    if df.empty:
        return 0

    if fuzzy is None:
        fuzzy = FuzzyMatcher(db)

    inserted = 0
    skipped = 0

    for _, row in df.iterrows():
        home_name = str(row["home_team"]).strip()
        away_name = str(row["away_team"]).strip()
        league_code = row["league_code"]
        season = row["season"]
        date_str = str(row["date"])[:10]

        # Resolve team IDs (upsert teams)
        home_id = _upsert_team(db, home_name, league_code, fuzzy)
        away_id = _upsert_team(db, away_name, league_code, fuzzy)

        # Check for duplicate match (same teams + same date)
        existing = db.fetchone(
            """SELECT id FROM matches
            WHERE home_team_id=? AND away_team_id=? AND date LIKE ?""",
            (home_id, away_id, f"{date_str}%"),
        )

        if existing:
            skipped += 1
            continue

        # Handle pandas NaN which floats when converting
        def to_int(x):
            if pd.isna(x) or x is None:
                return None
            return int(x)

        ft_home = to_int(row["ft_home_goals"])
        ft_away = to_int(row["ft_away_goals"])
        ht_home = to_int(row.get("ht_home_goals"))
        ht_away = to_int(row.get("ht_away_goals"))
        ft_result = row["ft_result"]

        db.execute(
            """INSERT INTO matches
            (home_team_id, away_team_id, date, season, league_code,
             ft_home_goals, ft_away_goals, ht_home_goals, ht_away_goals, ft_result)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (home_id, away_id, date_str, season, league_code,
             ft_home, ft_away, ht_home, ht_away, ft_result),
        )
        inserted += 1

    return inserted, skipped


def _upsert_team(db, name: str, league_code: str, fuzzy: FuzzyMatcher) -> int:
    """Find or create team, using fuzzy matching to avoid duplicates."""
    # Try exact match first
    existing = db.fetchone(
        "SELECT id FROM teams WHERE name=?", (name,)
    )
    if existing:
        return existing["id"]

    # Try fuzzy match against existing teams in same league
    league_teams = db.fetchall(
        "SELECT id, name FROM teams WHERE league_code=?", (league_code,)
    )
    if league_teams:
        best_match = fuzzy.match(
            name, [t["name"] for t in league_teams]
        )
        if best_match:
            matched_team = next(
                (t for t in league_teams if t["name"] == best_match), None
            )
            if matched_team:
                return matched_team["id"]

    # Create new team
    db.execute(
        "INSERT INTO teams (name, league_code) VALUES (?, ?)",
        (name, league_code),
    )
    new = db.fetchone("SELECT id FROM teams WHERE name=?", (name,))
    return new["id"]


def main():
    print("=" * 60)
    print("  Openfootball -> Database Ingestion")
    print("=" * 60)

    db = get_backend()
    db.connect()

    try:
        # Get current match count
        before = db.fetchone("SELECT COUNT(*) as cnt FROM matches")
        print(f"  Existing matches in DB: {before['cnt']}")

        # Load all openfootball data
        print("\n[1/2] Loading openfootball JSON data...")
        df = load_all_openfootball()

        if df.empty:
            print("[WARN] No openfootball data found. Run scripts/download_openfootball.py first!")
            return

        print(f"  Loaded {len(df)} matches from openfootball")

        # Ingest
        print("\n[2/2] Ingesting into database (deduplicating)...")
        fuzzy = FuzzyMatcher()
        inserted, skipped = ingest_openfootball(db, df, fuzzy)

        after = db.fetchone("SELECT COUNT(*) as cnt FROM matches")
        print(f"\n  NEW matches inserted: {inserted}")
        print(f"  Duplicates skipped: {skipped}")
        print(f"  Total matches in DB now: {after['cnt']}")

        # Summary by league
        print("\n  --- Per League ---")
        for league_code in sorted(df["league_code"].unique()):
            count = db.fetchone(
                "SELECT COUNT(*) as cnt FROM matches WHERE league_code=?",
                (league_code,),
            )
            print(f"  {league_code}: {count['cnt']} matches")

    finally:
        db.close()

    print("\n[DONE] Openfootball ingestion complete!")


if __name__ == "__main__":
    main()
