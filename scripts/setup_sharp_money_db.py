"""Setup SQLite Database Schema for Sharp Money & Line Movement."""
import sqlite3
import sys
from pathlib import Path

def update_sharp_money_db():
    db_path = Path("data/guzel_tahmin.db")
    if not db_path.exists():
        print(f"Error: Database not found at {db_path}")
        sys.exit(1)

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    print("Creating Sharp Money tables...")

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS wc_odds_movements (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            match_id INTEGER NOT NULL,
            snapshot_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            hours_to_kickoff INTEGER,
            odd_home NUMERIC(5,2),
            odd_draw NUMERIC(5,2),
            odd_away NUMERIC(5,2),
            ah_line NUMERIC(5,2),
            ah_home NUMERIC(5,2),
            ah_away NUMERIC(5,2),
            UNIQUE(match_id, hours_to_kickoff),
            FOREIGN KEY (match_id) REFERENCES matches(id)
        )
    """)

    conn.commit()
    conn.close()
    print("Sharp Money tables created successfully.")

if __name__ == "__main__":
    update_sharp_money_db()
