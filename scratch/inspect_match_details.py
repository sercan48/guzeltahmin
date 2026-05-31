import sqlite3

db_path = "data/guzel_tahmin.db"
conn = sqlite3.connect(db_path)
conn.row_factory = sqlite3.Row
cursor = conn.cursor()

cursor.execute("""
    SELECT m.id, m.date, t1.name as home, t2.name as away,
           m.pitch_type, m.travel_distance_km, m.cup_rotation_fatigue, m.dp_presence, m.weather_condition
    FROM matches m
    JOIN teams t1 ON m.home_team_id = t1.id
    JOIN teams t2 ON m.away_team_id = t2.id
    WHERE m.date LIKE '2026-05-29%'
""")
rows = cursor.fetchall()
for r in rows:
    print(dict(r))

conn.close()
