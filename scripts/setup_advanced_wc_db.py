"""Setup Advanced SQLite Database Schema for World Cup 2026."""
import sqlite3
import sys
from pathlib import Path

def update_advanced_db():
    db_path = Path("data/guzel_tahmin.db")
    if not db_path.exists():
        print(f"Error: Database not found at {db_path}")
        sys.exit(1)

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    print("Creating Advanced World Cup tables...")

    # wc_players: Required for Chemistry & Weakest Link Penalty
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS wc_players (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            team_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            position TEXT NOT NULL,
            club_name TEXT NOT NULL,
            international_caps INTEGER DEFAULT 0,
            ea_fc_rating INTEGER NOT NULL,
            FOREIGN KEY (team_id) REFERENCES teams(id)
        )
    """)

    # wc_venue_distances: Required for Travel Fatigue
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS wc_venue_distances (
            venue_a_id INTEGER NOT NULL,
            venue_b_id INTEGER NOT NULL,
            distance_km INTEGER NOT NULL,
            PRIMARY KEY (venue_a_id, venue_b_id),
            FOREIGN KEY (venue_a_id) REFERENCES venues(id),
            FOREIGN KEY (venue_b_id) REFERENCES venues(id)
        )
    """)

    # wc_match_lineups: More granular than previous match_lineups to hold individual player data
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS wc_match_lineups (
            match_id INTEGER NOT NULL,
            team_id INTEGER NOT NULL,
            player_id INTEGER NOT NULL,
            is_starting BOOLEAN DEFAULT 1,
            PRIMARY KEY (match_id, team_id, player_id),
            FOREIGN KEY (match_id) REFERENCES matches(id),
            FOREIGN KEY (team_id) REFERENCES teams(id),
            FOREIGN KEY (player_id) REFERENCES wc_players(id)
        )
    """)

    conn.commit()
    conn.close()
    print("Advanced World Cup tables created successfully.")

if __name__ == "__main__":
    update_advanced_db()
