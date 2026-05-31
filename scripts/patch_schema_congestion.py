import sqlite3
from pathlib import Path

def patch_schema():
    db_path = Path("data/guzel_tahmin.db")
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    print("Patching matches table with congestion_advantage...")
    
    try:
        cursor.execute("ALTER TABLE matches ADD COLUMN congestion_advantage REAL DEFAULT 0.0")
        print("Successfully added congestion_advantage.")
    except sqlite3.OperationalError as e:
        if "duplicate column name" in str(e):
            print("Column congestion_advantage already exists. Skipping.")
        else:
            print(f"Error: {e}")

    conn.commit()
    conn.close()

if __name__ == "__main__":
    patch_schema()
