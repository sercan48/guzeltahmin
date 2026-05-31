import sqlite3
from pathlib import Path

def migrate():
    db_path = Path("data/guzel_tahmin.db")
    print(f"Connecting to database at {db_path}...")
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # Get initial counts
    cursor.execute("SELECT league_code, COUNT(*) FROM matches GROUP BY league_code")
    print("Initial matches count:", cursor.fetchall())

    # Update matches
    cursor.execute("UPDATE matches SET league_code='NORWAY_ELITESERIEN' WHERE league_code='Eliteserien'")
    print(f"Updated matches 'Eliteserien' -> 'NORWAY_ELITESERIEN': {cursor.rowcount} rows affected.")

    cursor.execute("UPDATE matches SET league_code='BRAZIL_SERIE_A' WHERE league_code='Serie A'")
    print(f"Updated matches 'Serie A' -> 'BRAZIL_SERIE_A': {cursor.rowcount} rows affected.")

    # Update teams
    cursor.execute("UPDATE teams SET league_code='NORWAY_ELITESERIEN' WHERE league_code='Eliteserien'")
    print(f"Updated teams 'Eliteserien' -> 'NORWAY_ELITESERIEN': {cursor.rowcount} rows affected.")

    cursor.execute("UPDATE teams SET league_code='BRAZIL_SERIE_A' WHERE league_code='Serie A'")
    print(f"Updated teams 'Serie A' -> 'BRAZIL_SERIE_A': {cursor.rowcount} rows affected.")

    conn.commit()

    # Get final counts
    cursor.execute("SELECT league_code, COUNT(*) FROM matches GROUP BY league_code")
    print("Final matches count:", cursor.fetchall())

    conn.close()
    print("Migration complete!")

if __name__ == "__main__":
    migrate()
