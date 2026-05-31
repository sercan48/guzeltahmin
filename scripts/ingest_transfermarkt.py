"""Ingest Transfermarkt appearances data to enhance xG calculations.

Reads appearances.csv (1.8M+ records) containing:
- player_id, game_id, goals, assists, minutes_played, yellow/red cards
Aggregates per-team goal stats for historical seasons to feed the xG engine.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pandas as pd
from src.db.base import get_backend
from src.ingestion.fuzzy_matcher import FuzzyMatcher


TM_DIR = Path(__file__).parent.parent / "data" / "transfermarkt"


def load_appearances():
    """Load and return appearances DataFrame."""
    path = TM_DIR / "appearances.csv"
    if not path.exists():
        raise FileNotFoundError(f"appearances.csv not found at {path}")
    
    cols = ["player_id", "player_current_club_id", "goals", "assists",
            "minutes_played", "yellow_cards", "red_cards", "date"]
    df = pd.read_csv(path, usecols=cols, low_memory=False)
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    return df


def load_clubs_mapping():
    """Load clubs.csv to map club_id -> club_name."""
    path = TM_DIR / "clubs.csv"
    df = pd.read_csv(path, usecols=["club_id", "name", "domestic_competition_id"], low_memory=False)
    return df


def main():
    print("=" * 60)
    print("  Transfermarkt Appearances -> xG Enhancement")
    print("=" * 60)

    db = get_backend()
    db.connect()
    fuzzy = FuzzyMatcher()

    # Load DB teams
    db_teams = db.fetchall("SELECT id, name FROM teams")
    team_names = [t["name"] for t in db_teams]
    team_map = {t["name"]: t["id"] for t in db_teams}

    print("[1/4] Loading appearances.csv...")
    apps = load_appearances()
    print(f"  Loaded {len(apps):,} appearance records")

    print("[2/4] Loading clubs.csv for name mapping...")
    clubs = load_clubs_mapping()
    club_id_to_name = dict(zip(clubs["club_id"], clubs["name"]))

    # Map TM club_id -> our team_id via fuzzy matching
    print("[3/4] Matching Transfermarkt clubs to our DB teams...")
    tm_to_db = {}
    matched = 0
    for cid, cname in club_id_to_name.items():
        if pd.isna(cname):
            continue
        match = fuzzy.match(str(cname), team_names)
        if match:
            tm_to_db[cid] = team_map[match]
            matched += 1

    print(f"  Matched {matched} / {len(club_id_to_name)} TM clubs to our DB")

    # Aggregate goals per team per season
    print("[4/4] Aggregating goal statistics per team-season...")
    apps["season"] = apps["date"].dt.year.apply(
        lambda y: f"{y-1}-{y}" if pd.notna(y) else None
    )
    apps = apps.dropna(subset=["season", "player_current_club_id"])
    
    # Filter only matched clubs
    apps["db_team_id"] = apps["player_current_club_id"].map(tm_to_db)
    apps = apps.dropna(subset=["db_team_id"])
    apps["db_team_id"] = apps["db_team_id"].astype(int)

    agg = apps.groupby(["db_team_id", "season"]).agg(
        total_goals=("goals", "sum"),
        total_assists=("assists", "sum"),
        total_minutes=("minutes_played", "sum"),
        total_yellows=("yellow_cards", "sum"),
        total_reds=("red_cards", "sum"),
        appearance_count=("player_id", "count"),
    ).reset_index()

    # Store aggregated stats in DB
    db.execute("""
        CREATE TABLE IF NOT EXISTS team_season_stats (
            team_id INTEGER,
            season TEXT,
            total_goals INTEGER DEFAULT 0,
            total_assists INTEGER DEFAULT 0,
            total_minutes INTEGER DEFAULT 0,
            total_yellows INTEGER DEFAULT 0,
            total_reds INTEGER DEFAULT 0,
            appearance_count INTEGER DEFAULT 0,
            goals_per_90 REAL DEFAULT 0,
            PRIMARY KEY (team_id, season)
        )
    """)

    inserted = 0
    for _, row in agg.iterrows():
        g90 = (row["total_goals"] / max(row["total_minutes"], 1)) * 90
        try:
            db.execute(
                """INSERT OR REPLACE INTO team_season_stats 
                   (team_id, season, total_goals, total_assists, total_minutes,
                    total_yellows, total_reds, appearance_count, goals_per_90)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (int(row["db_team_id"]), row["season"],
                 int(row["total_goals"]), int(row["total_assists"]),
                 int(row["total_minutes"]), int(row["total_yellows"]),
                 int(row["total_reds"]), int(row["appearance_count"]),
                 round(g90, 4))
            )
            inserted += 1
        except Exception:
            pass

    db.close()

    print(f"\n[OK] Inserted {inserted} team-season stat records into DB")
    print(f"  Sample goals_per_90 values:")
    top = agg.nlargest(5, "total_goals")
    for _, r in top.iterrows():
        g90 = (r["total_goals"] / max(r["total_minutes"], 1)) * 90
        print(f"  Team {int(r['db_team_id'])}: {int(r['total_goals'])} goals, {g90:.2f} g/90")


if __name__ == "__main__":
    main()
