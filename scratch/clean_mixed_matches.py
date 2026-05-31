import sqlite3
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.ingestion.venue_registry import get_team_venue

def cleanup():
    db_path = Path("data/guzel_tahmin.db")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    # Fetch all matches in NORWAY_ELITESERIEN and BRAZIL_SERIE_A
    cursor.execute("""
        SELECT m.id, m.league_code, ht.name as home, at.name as away 
        FROM matches m 
        JOIN teams ht ON m.home_team_id = ht.id 
        JOIN teams at ON m.away_team_id = at.id 
        WHERE m.league_code IN ('NORWAY_ELITESERIEN', 'BRAZIL_SERIE_A')
    """)
    rows = cursor.fetchall()
    print(f"Total summer league matches in DB: {len(rows)}")

    to_delete = []
    for r in rows:
        home_venue = get_team_venue(r["home"])
        away_venue = get_team_venue(r["away"])
        
        # If neither team has a registered venue for summer leagues, it is a mixed-up match
        if home_venue is None and away_venue is None:
            to_delete.append(r["id"])

    print(f"Detected {len(to_delete)} mixed-up matches to delete.")

    if to_delete:
        # Delete in chunks
        placeholders = ",".join("?" for _ in to_delete)
        cursor.execute(f"DELETE FROM matches WHERE id IN ({placeholders})", to_delete)
        print(f"Successfully deleted {cursor.rowcount} corrupt matches.")
        
        # Also clean up odds table where match_id matches deleted matches
        cursor.execute(f"DELETE FROM odds WHERE match_id IN ({placeholders})", to_delete)
        print(f"Successfully deleted {cursor.rowcount} associated odds records.")
        
        # Also clean up predictions table where match_id matches deleted matches
        cursor.execute(f"DELETE FROM predictions WHERE match_id IN ({placeholders})", to_delete)
        print(f"Successfully deleted {cursor.rowcount} associated predictions records.")

        conn.commit()

    # Get final counts
    cursor.execute("SELECT league_code, COUNT(*) FROM matches GROUP BY league_code")
    print("Final match counts in DB:", cursor.fetchall())

    conn.close()
    print("Cleanup complete!")

if __name__ == "__main__":
    cleanup()
