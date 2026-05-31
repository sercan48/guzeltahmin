import sqlite3

db_path = "data/guzel_tahmin.db"
conn = sqlite3.connect(db_path)
conn.row_factory = sqlite3.Row
cursor = conn.cursor()

cursor.execute("SELECT COUNT(*) as cnt FROM matches")
print(f"Total matches in DB: {cursor.fetchone()['cnt']}")

cursor.execute("""
    SELECT league_code, COUNT(*) as total,
           SUM(CASE WHEN ft_result IS NOT NULL THEN 1 ELSE 0 END) as finished
    FROM matches
    GROUP BY league_code
""")
print("\n=== Matches by League ===")
for r in cursor.fetchall():
    print(f"League: {r['league_code']} | Total: {r['total']} | Finished: {r['finished']}")

conn.close()
