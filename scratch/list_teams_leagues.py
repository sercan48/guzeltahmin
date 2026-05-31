import sqlite3

db_path = "data/guzel_tahmin.db"
conn = sqlite3.connect(db_path)
conn.row_factory = sqlite3.Row
cursor = conn.cursor()

cursor.execute("SELECT league_code, COUNT(*) as cnt FROM teams GROUP BY league_code")
for r in cursor.fetchall():
    print(f"League in teams table: {r['league_code']} | Count: {r['cnt']}")

conn.close()
