import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.db.base import get_backend

db = get_backend()
db.connect()

# Query matches between 29th and 31st May 2026
query = """
    SELECT m.id, m.date, m.league_code, t1.name as home_team, t2.name as away_team 
    FROM matches m
    JOIN teams t1 ON m.home_team_id = t1.id
    JOIN teams t2 ON m.away_team_id = t2.id
    WHERE DATE(m.date) BETWEEN '2026-05-29' AND '2026-05-31'
    ORDER BY m.date ASC
"""
matches = db.fetchall(query)

print("=" * 60)
print(f"  Matches in Database for 29-30-31 May 2026: {len(matches)}")
print("=" * 60)

for m in matches:
    print(f"[{m['league_code']}] {m['date']} - {m['home_team']} vs {m['away_team']} (ID: {m['id']})")

db.close()
