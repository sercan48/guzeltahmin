"""Setup SQLite Database Schema for Summer Leagues Hybrid Features."""
import sqlite3
import sys
from pathlib import Path

def update_summer_league_db():
    db_path = Path("data/guzel_tahmin.db")
    if not db_path.exists():
        print(f"Error: Database not found at {db_path}")
        sys.exit(1)

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    print("Adding Summer League specific features to matches table...")

    # We add columns with defaults so existing European matches aren't broken.
    columns_to_add = [
        "pitch_type TEXT DEFAULT 'NATURAL'",
        "travel_distance_km REAL DEFAULT 0",
        "cup_rotation_fatigue BOOLEAN DEFAULT 0",
        "dp_presence REAL DEFAULT 0.0",
        "weather_condition TEXT DEFAULT 'NORMAL'",
        "is_summer_league BOOLEAN DEFAULT 0"
    ]

    for col in columns_to_add:
        col_name = col.split()[0]
        try:
            cursor.execute(f"ALTER TABLE matches ADD COLUMN {col}")
            print(f"Added column {col_name}")
        except sqlite3.OperationalError as e:
            if "duplicate column name" in str(e):
                print(f"Column {col_name} already exists. Skipping.")
            else:
                print(f"Error adding {col_name}: {e}")

    conn.commit()
    conn.close()
    print("Summer League DB setup completed.")

if __name__ == "__main__":
    update_summer_league_db()
