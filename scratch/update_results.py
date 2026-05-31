import sqlite3

db_path = "data/guzel_tahmin.db"
conn = sqlite3.connect(db_path)
cursor = conn.cursor()

# Actual results of 2026-05-29 matches
results = [
    {"home": "Aalesund", "away": "HamKam", "hg": 2, "ag": 2, "res": "D"},
    {"home": "Brann", "away": "Sarpsborg 08", "hg": 1, "ag": 2, "res": "A"},
    {"home": "Fredrikstad", "away": "Start", "hg": 2, "ag": 1, "res": "H"},
    {"home": "KFUM Oslo", "away": "Tromso", "hg": 0, "ag": 0, "res": "D"},
    {"home": "Rosenborg", "away": "Bodo/Glimt", "hg": 2, "ag": 2, "res": "D"},
    {"home": "Valerenga", "away": "Kristiansund", "hg": 3, "ag": 1, "res": "H"}
]

print("=== Updating match results in DB ===")
updated_count = 0
for match in results:
    cursor.execute("""
        UPDATE matches
        SET ft_home_goals = ?, ft_away_goals = ?, ft_result = ?
        WHERE date LIKE '2026-05-29%' AND home_team_id = (SELECT id FROM teams WHERE name = ?) AND away_team_id = (SELECT id FROM teams WHERE name = ?)
    """, (match["hg"], match["ag"], match["res"], match["home"], match["away"]))
    
    if cursor.rowcount > 0:
        print(f"Updated: {match['home']} vs {match['away']} -> {match['hg']}-{match['ag']} ({match['res']})")
        updated_count += 1
    else:
        print(f"Not found: {match['home']} vs {match['away']}")

conn.commit()
print(f"Total updated: {updated_count}/6")

# Also let's update actual_result in predictions table
cursor.execute("""
    UPDATE predictions
    SET actual_result = (SELECT ft_result FROM matches WHERE matches.id = predictions.match_id)
    WHERE match_id IN (
        SELECT id FROM matches WHERE date LIKE '2026-05-29%'
    )
""")
conn.commit()
print("Updated actual_result in predictions table.")

conn.close()
