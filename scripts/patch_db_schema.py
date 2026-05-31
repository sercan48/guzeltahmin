import sqlite3
from pathlib import Path

def patch_db():
    db_path = Path("data/guzel_tahmin.db")
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    # Check current columns
    cursor.execute("PRAGMA table_info(matches)")
    columns = [col[1] for col in cursor.fetchall()]
    
    if "api_match_id" not in columns:
        print("Adding api_match_id column to matches table...")
        cursor.execute("ALTER TABLE matches ADD COLUMN api_match_id INTEGER")
        
    if "time" not in columns:
        print("Adding time column to matches table...")
        cursor.execute("ALTER TABLE matches ADD COLUMN time TEXT")
        
    conn.commit()
    conn.close()
    print("Database patched successfully.")

if __name__ == "__main__":
    patch_db()
