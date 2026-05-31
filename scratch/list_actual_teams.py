import sqlite3

db_path = "data/guzel_tahmin.db"
conn = sqlite3.connect(db_path)
conn.row_factory = sqlite3.Row
cursor = conn.cursor()

for league in ["Eliteserien", "Serie A"]:
    cursor.execute("SELECT DISTINCT name FROM teams WHERE league_code = ?", (league,))
    rows = cursor.fetchall()
    print(f"\n=== Teams in {league} ({len(rows)}) ===")
    teams = [r["name"] for r in rows]
    print(teams)

conn.close()
