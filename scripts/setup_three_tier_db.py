"""Setup SQLite Database Schema for Three-Tier Scheduling."""
import sqlite3
import sys
from pathlib import Path

def update_three_tier_db():
    db_path = Path("data/guzel_tahmin.db")
    if not db_path.exists():
        print(f"Error: Database not found at {db_path}")
        sys.exit(1)

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    print("Adding Three-Tier status flags to matches table...")

    columns_to_add = [
        "is_preliminary_run BOOLEAN DEFAULT 0",
        "is_night_run BOOLEAN DEFAULT 0",
        "is_official_run BOOLEAN DEFAULT 0"
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
    print("Three-Tier DB setup completed.")

if __name__ == "__main__":
    update_three_tier_db()
