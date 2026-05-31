import sqlite3

db_path = "data/guzel_tahmin.db"
conn = sqlite3.connect(db_path)
conn.row_factory = sqlite3.Row
cursor = conn.cursor()

print("--- Checking unplayed Norway matches in DB ---")
cursor.execute("""
    SELECT m.id, m.date, m.league_code, t1.name as home, t2.name as away, m.ft_result
    FROM matches m
    JOIN teams t1 ON m.home_team_id = t1.id
    JOIN teams t2 ON m.away_team_id = t2.id
    WHERE m.league_code = 'NORWAY_ELITESERIEN' AND m.ft_result IS NULL;
""")
rows = cursor.fetchall()
print(f"Unplayed Norway matches: {len(rows)}")
for r in rows[:10]:
    print(dict(r))

print("\n--- Checking Norway predictions ---")
cursor.execute("""
    SELECT p.*, m.date, t1.name as home, t2.name as away
    FROM predictions p
    JOIN matches m ON p.match_id = m.id
    JOIN teams t1 ON m.home_team_id = t1.id
    JOIN teams t2 ON m.away_team_id = t2.id
    WHERE m.league_code = 'NORWAY_ELITESERIEN';
""")
rows = cursor.fetchall()
print(f"Predictions for Norway: {len(rows)}")
for r in rows[:5]:
    print(f"Match: {r['home']} vs {r['away']} | Date: {r['date']} | Pred: {r['predicted_result']} | Conf: {r['confidence_score']:.1f}%")

conn.close()
