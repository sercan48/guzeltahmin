"""
Patch SQLite Schema to add Elo and xG columns for Autonomous Data Ingestion.
"""
import sqlite3
from pathlib import Path

def patch_schema():
    db_path = Path("data/guzel_tahmin.db")
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    print("Patching database schema with Elo and xG columns...")
    
    columns = [
        "home_elo REAL DEFAULT 1500.0",
        "away_elo REAL DEFAULT 1500.0",
        "home_xg REAL DEFAULT 0.0",
        "away_xg REAL DEFAULT 0.0"
    ]
    
    for col in columns:
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
    print("Schema patch completed.")

if __name__ == "__main__":
    patch_schema()
