"""Update SQLite Database with World Cup 2026 tables."""
import sqlite3
import sys
from pathlib import Path

def update_db():
    db_path = Path("data/guzel_tahmin.db")
    if not db_path.exists():
        print(f"Error: Database not found at {db_path}")
        sys.exit(1)

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    print("Creating World Cup tables...")

    # Create Venues table for Environmental Factors (Altitude, Climate)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS venues (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            city TEXT NOT NULL,
            country TEXT NOT NULL,
            altitude_meters INTEGER DEFAULT 0,
            climate_type TEXT,
            timezone TEXT
        )
    """)

    # Create Match Lineups table for EA FC Ratings and Squad Quality
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS match_lineups (
            match_id INTEGER NOT NULL,
            team_id INTEGER NOT NULL,
            avg_ea_fc_rating REAL,
            total_market_value INTEGER,
            star_player_count INTEGER DEFAULT 0,
            is_confirmed BOOLEAN DEFAULT 0,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (match_id, team_id),
            FOREIGN KEY (match_id) REFERENCES matches(id),
            FOREIGN KEY (team_id) REFERENCES teams(id)
        )
    """)

    # Alter matches table if needed, e.g., to add venue_id and stage
    # Check if venue_id exists
    cursor.execute("PRAGMA table_info(matches)")
    columns = [col[1] for col in cursor.fetchall()]
    
    if "venue_id" not in columns:
        print("Adding venue_id to matches table...")
        cursor.execute("ALTER TABLE matches ADD COLUMN venue_id INTEGER REFERENCES venues(id)")
    
    if "stage" not in columns:
        print("Adding stage to matches table...")
        cursor.execute("ALTER TABLE matches ADD COLUMN stage TEXT DEFAULT 'Regular'")

    # Check teams table
    cursor.execute("PRAGMA table_info(teams)")
    t_columns = [col[1] for col in cursor.fetchall()]
    
    if "elo_rating" not in t_columns:
        print("Adding elo_rating to teams table...")
        cursor.execute("ALTER TABLE teams ADD COLUMN elo_rating INTEGER DEFAULT 1500")
        
    if "continent" not in t_columns:
        print("Adding continent to teams table...")
        cursor.execute("ALTER TABLE teams ADD COLUMN continent TEXT DEFAULT 'Unknown'")

    conn.commit()
    conn.close()
    print("Database update complete. World Cup 2026 tables are ready.")

if __name__ == "__main__":
    update_db()
