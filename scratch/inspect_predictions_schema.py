import sqlite3

db_path = "data/guzel_tahmin.db"
conn = sqlite3.connect(db_path)
conn.row_factory = sqlite3.Row
cursor = conn.cursor()

print("=== Matches and Predictions on 2026-05-29 ===")
cursor.execute("""
    SELECT m.id, m.date, m.league_code, t1.name as home, t2.name as away, m.ft_result, m.ft_home_goals, m.ft_away_goals,
           p.predicted_result, p.confidence_score, p.home_win_prob, p.draw_prob, p.away_win_prob,
           p.top_1_pick, p.top_1_type, p.top_1_success,
           p.top_2_pick, p.top_2_type, p.top_2_success
    FROM matches m
    JOIN teams t1 ON m.home_team_id = t1.id
    JOIN teams t2 ON m.away_team_id = t2.id
    LEFT JOIN predictions p ON p.match_id = m.id
    WHERE m.date LIKE '2026-05-29%' OR m.date LIKE '2026-05-28%' OR m.date LIKE '2026-05-30%'
""")
rows = cursor.fetchall()
print(f"Total matches around 2026-05-29: {len(rows)}")
for r in rows:
    print(dict(r))

conn.close()
