import sqlite3

conn = sqlite3.connect("data/guzel_tahmin.db")
conn.row_factory = sqlite3.Row
cursor = conn.cursor()

print("=== SP2 matches >= 2026-05-20 ===")
cursor.execute("""
    SELECT m.id, m.date, t1.name as home, t2.name as away, m.ft_result
    FROM matches m
    JOIN teams t1 ON m.home_team_id = t1.id
    JOIN teams t2 ON m.away_team_id = t2.id
    WHERE m.league_code = 'SP2' AND m.date >= '2026-05-20'
    ORDER BY m.date ASC
""")
for r in cursor.fetchall():
    print(f"  {r['date']} | {r['home']} vs {r['away']} | Result: {r['ft_result']}")

conn.close()
