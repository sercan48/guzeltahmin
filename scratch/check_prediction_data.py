import sqlite3

db_path = "data/guzel_tahmin.db"
conn = sqlite3.connect(db_path)
cursor = conn.cursor()

cursor.execute("""
    SELECT COUNT(*) as total,
           SUM(CASE WHEN m.ft_result IS NOT NULL THEN 1 ELSE 0 END) as finished
    FROM predictions p
    JOIN matches m ON p.match_id = m.id
""")
row = cursor.fetchone()
print(f"Total predictions: {row[0]}, Finished matches: {row[1]}")

# Let's see by league
cursor.execute("""
    SELECT m.league_code, COUNT(*) as total,
           SUM(CASE WHEN m.ft_result IS NOT NULL THEN 1 ELSE 0 END) as finished
    FROM predictions p
    JOIN matches m ON p.match_id = m.id
    GROUP BY m.league_code
""")
for r in cursor.fetchall():
    print(f"League: {r[0]} | Total: {r[1]} | Finished: {r[2]}")

conn.close()
