import sqlite3

conn = sqlite3.connect("data/guzel_tahmin.db")
conn.row_factory = sqlite3.Row
cursor = conn.cursor()

# Get teams associated with matches that have league_code = 'NORWAY_ELITESERIEN'
cursor.execute("""
    SELECT DISTINCT t.id, t.name, t.league_code
    FROM teams t
    JOIN matches m ON (m.home_team_id = t.id OR m.away_team_id = t.id)
    WHERE m.league_code = 'NORWAY_ELITESERIEN'
""")
rows = cursor.fetchall()
print(f"Norway Eliteserien teams found via matches: {len(rows)}")
for r in rows:
    print(f"  - Team ID: {r['id']} | Name: {r['name']} | Team League Code: {r['league_code']}")

conn.close()
