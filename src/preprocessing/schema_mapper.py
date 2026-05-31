"""Map cleaned data into database schema and persist."""

import pandas as pd
import json
import math
import logging

from src.ingestion.csv_loader import parse_date, extract_odds
from src.preprocessing.cleaner import full_clean_pipeline
from src.ingestion.venue_registry import get_team_venue

logger = logging.getLogger(__name__)

# List of summer league codes to validate/adjust
SUMMER_LEAGUE_CODES = [
    "NORWAY_ELITESERIEN", "Eliteserien",
    "BRAZIL_SERIE_A", "Serie A",
    "SWEDEN_ALLSVENSKAN", "Allsvenskan",
    "FINLAND_VEIKKAUSLIIGA", "Veikkausliiga"
]

def haversine_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculate the great-circle distance between two points in km."""
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return round(R * c, 1)

def _ensure_integrity_log_table(db):
    """Ensure that the feature_integrity_log table exists in the database."""
    db.execute("""
        CREATE TABLE IF NOT EXISTS feature_integrity_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            match_id INTEGER,
            date TEXT,
            league_code TEXT,
            home_team TEXT,
            away_team TEXT,
            missing_field TEXT,
            severity TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

def ingest_matches_to_db(df: pd.DataFrame, db) -> int:
    """Process cleaned match DataFrame and insert into database.

    Steps:
    1. Ensure teams exist, get IDs
    2. Insert matches with team IDs
    3. Extract and insert odds
    4. Extract and insert referee stats

    Returns:
        Number of matches inserted.
    """
    df = parse_date(df)
    df = full_clean_pipeline(df)

    _ensure_integrity_log_table(db)

    inserted = 0

    for _, row in df.iterrows():
        home_team = str(row.get("home_team", "")).strip()
        away_team = str(row.get("away_team", "")).strip()
        league_code = str(row.get("league_code", "")).strip()

        if not home_team or not away_team:
            continue

        # ── SUMMER LEAGUE GEOMETRY CALCULATIONS ──
        is_summer_league = 1 if league_code in SUMMER_LEAGUE_CODES else 0
        pitch_type = "NATURAL"
        travel_distance = None

        home_venue = get_team_venue(home_team)
        away_venue = get_team_venue(away_team)

        if home_venue:
            pitch_type = home_venue.get("pitch", "NATURAL")

        if home_venue and away_venue:
            travel_distance = haversine_distance(
                home_venue["lat"], home_venue["lon"],
                away_venue["lat"], away_venue["lon"]
            )

        # Upsert teams
        home_id = _ensure_team(db, home_team, league_code)
        away_id = _ensure_team(db, away_team, league_code)

        # Check if duplicate/existing
        existing = db.fetchone(
            "SELECT id, ft_result, pitch_type, travel_distance_km, is_summer_league "
            "FROM matches WHERE date=? AND home_team_id=? AND away_team_id=?",
            (str(row.get("date", ""))[:10], home_id, away_id),
        )

        # Validation logger helper
        def _log_integrity_errors(match_idx):
            if is_summer_league:
                if not pitch_type:
                    db.execute(
                        "INSERT INTO feature_integrity_log (match_id, date, league_code, home_team, away_team, missing_field, severity) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (match_idx, str(row.get("date", ""))[:10], league_code, home_team, away_team, "pitch_type", "WARNING")
                    )
                if travel_distance is None or travel_distance == 0:
                    db.execute(
                        "INSERT INTO feature_integrity_log (match_id, date, league_code, home_team, away_team, missing_field, severity) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (match_idx, str(row.get("date", ""))[:10], league_code, home_team, away_team, "travel_distance_km", "WARNING")
                    )

        if existing:
            # If match exists but has no result, and row has a result, update it
            if not existing.get("ft_result") and row.get("ft_result") and str(row.get("ft_result")).strip() in ('H', 'D', 'A'):
                db.execute(
                    """UPDATE matches SET
                        ft_home_goals = ?, ft_away_goals = ?, ft_result = ?,
                        ht_home_goals = ?, ht_away_goals = ?,
                        home_shots = ?, away_shots = ?, home_shots_target = ?, away_shots_target = ?,
                        home_corners = ?, away_corners = ?, home_fouls = ?, away_fouls = ?,
                        home_yellows = ?, away_yellows = ?, home_reds = ?, away_reds = ?, referee = ?,
                        pitch_type = ?, travel_distance_km = ?, is_summer_league = ?
                       WHERE id = ?""",
                    (
                        int(row.get("ft_home_goals", 0)),
                        int(row.get("ft_away_goals", 0)),
                        str(row.get("ft_result", "")),
                        int(row.get("ht_home_goals", 0)),
                        int(row.get("ht_away_goals", 0)),
                        int(row.get("home_shots", 0)),
                        int(row.get("away_shots", 0)),
                        int(row.get("home_shots_target", 0)),
                        int(row.get("away_shots_target", 0)),
                        int(row.get("home_corners", 0)),
                        int(row.get("away_corners", 0)),
                        int(row.get("home_fouls", 0)),
                        int(row.get("away_fouls", 0)),
                        int(row.get("home_yellows", 0)),
                        int(row.get("away_yellows", 0)),
                        int(row.get("home_reds", 0)),
                        int(row.get("away_reds", 0)),
                        str(row.get("referee", "Unknown")),
                        pitch_type, travel_distance, is_summer_league,
                        existing["id"]
                    )
                )
                _log_integrity_errors(existing["id"])
                _insert_odds(db, existing["id"], row)
                _update_referee(db, row)
                inserted += 1
            else:
                # Update summer league metadata if it changed or is missing
                if (existing["pitch_type"] != pitch_type or
                    existing["travel_distance_km"] != travel_distance or
                    existing["is_summer_league"] != is_summer_league):
                    db.execute(
                        "UPDATE matches SET pitch_type = ?, travel_distance_km = ?, is_summer_league = ? "
                        "WHERE id = ?",
                        (pitch_type, travel_distance, is_summer_league, existing["id"])
                    )
            continue

        # Insert match
        match_id = db.execute(
            """INSERT INTO matches (
                date, league_code, season, home_team_id, away_team_id,
                ft_home_goals, ft_away_goals, ft_result,
                ht_home_goals, ht_away_goals,
                home_shots, away_shots, home_shots_target, away_shots_target,
                home_corners, away_corners, home_fouls, away_fouls,
                home_yellows, away_yellows, home_reds, away_reds, referee,
                pitch_type, travel_distance_km, is_summer_league
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                str(row.get("date", ""))[:10],
                league_code,
                str(row.get("season", "")),
                home_id, away_id,
                int(row.get("ft_home_goals", 0)),
                int(row.get("ft_away_goals", 0)),
                str(row.get("ft_result", "")),
                int(row.get("ht_home_goals", 0)),
                int(row.get("ht_away_goals", 0)),
                int(row.get("home_shots", 0)),
                int(row.get("away_shots", 0)),
                int(row.get("home_shots_target", 0)),
                int(row.get("away_shots_target", 0)),
                int(row.get("home_corners", 0)),
                int(row.get("away_corners", 0)),
                int(row.get("home_fouls", 0)),
                int(row.get("away_fouls", 0)),
                int(row.get("home_yellows", 0)),
                int(row.get("away_yellows", 0)),
                int(row.get("home_reds", 0)),
                int(row.get("away_reds", 0)),
                str(row.get("referee", "Unknown")),
                pitch_type, travel_distance, is_summer_league
            ),
        ).lastrowid

        _log_integrity_errors(match_id)

        # Insert odds
        _insert_odds(db, match_id, row)

        # Track referee
        _update_referee(db, row)

        inserted += 1

    return inserted


def _ensure_team(db, name: str, league_code: str) -> int:
    """Get or create team, return ID."""
    existing = db.fetchone("SELECT id FROM teams WHERE name=?", (name,))
    if existing:
        return existing["id"]

    cursor = db.execute(
        "INSERT INTO teams (name, league_code, aliases) VALUES (?, ?, ?)",
        (name, league_code, json.dumps([name])),
    )
    return cursor.lastrowid


def _insert_odds(db, match_id: int, row: pd.Series):
    """Insert odds for a match from available bookmaker columns."""
    # Pinnacle odds
    pin_h = _safe_float(row.get("pin_home"))
    pin_d = _safe_float(row.get("pin_draw"))
    pin_a = _safe_float(row.get("pin_away"))
    if pin_h and pin_d and pin_a:
        db.execute(
            """INSERT OR IGNORE INTO odds
            (match_id, bookmaker, home_odds, draw_odds, away_odds)
            VALUES (?, ?, ?, ?, ?)""",
            (match_id, "Pinnacle", pin_h, pin_d, pin_a),
        )

    # Bet365 odds
    b365_h = _safe_float(row.get("b365_home"))
    b365_d = _safe_float(row.get("b365_draw"))
    b365_a = _safe_float(row.get("b365_away"))
    if b365_h and b365_d and b365_a:
        db.execute(
            """INSERT OR IGNORE INTO odds
            (match_id, bookmaker, home_odds, draw_odds, away_odds, over25_odds, under25_odds)
            VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                match_id, "Bet365", b365_h, b365_d, b365_a,
                _safe_float(row.get("avg_over25")),
                _safe_float(row.get("avg_under25")),
            ),
        )


def _update_referee(db, row: pd.Series):
    """Update referee statistics incrementally."""
    ref_name = str(row.get("referee", "")).strip()
    league_code = str(row.get("league_code", "")).strip()
    if not ref_name or ref_name == "Unknown":
        return

    yellows = int(row.get("home_yellows", 0)) + int(row.get("away_yellows", 0))
    reds = int(row.get("home_reds", 0)) + int(row.get("away_reds", 0))
    fouls = int(row.get("home_fouls", 0)) + int(row.get("away_fouls", 0))

    existing = db.fetchone(
        "SELECT id, avg_yellows, avg_reds, avg_fouls, match_count FROM referees WHERE name=? AND league_code=?",
        (ref_name, league_code),
    )

    if existing:
        n = existing["match_count"]
        new_n = n + 1
        db.execute(
            """UPDATE referees SET
                avg_yellows = (avg_yellows * ? + ?) / ?,
                avg_reds = (avg_reds * ? + ?) / ?,
                avg_fouls = (avg_fouls * ? + ?) / ?,
                match_count = ?
            WHERE id = ?""",
            (
                n, yellows, new_n,
                n, reds, new_n,
                n, fouls, new_n,
                new_n,
                existing["id"],
            ),
        )
    else:
        db.execute(
            """INSERT INTO referees (name, league_code, avg_yellows, avg_reds, avg_fouls, match_count)
            VALUES (?, ?, ?, ?, ?, 1)""",
            (ref_name, league_code, yellows, reds, fouls),
        )


def _safe_float(val) -> float | None:
    """Safely convert to float, return None on failure."""
    try:
        f = float(val)
        return f if f > 0 else None
    except (TypeError, ValueError):
        return None
