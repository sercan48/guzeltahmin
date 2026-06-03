import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.db.base import get_backend
import datetime

db = get_backend()
db.connect()
try:
    print("--- 1. Future matches (ft_result IS NULL) in next 7 days ---")
    matches = db.fetchall("""
        SELECT m.id, m.date, m.league_code, t1.name as home, t2.name as away 
        FROM matches m
        JOIN teams t1 ON m.home_team_id = t1.id
        JOIN teams t2 ON m.away_team_id = t2.id
        WHERE m.ft_result IS NULL 
        AND DATE(m.date) >= DATE('now')
        ORDER BY m.date LIMIT 20
    """)
    for m in matches:
        print(f"  {m['date']} | {m['league_code']} | {m['home']} vs {m['away']}")
        
    print("\n--- 2. All matches in June 2026 ---")
    matches_june = db.fetchall("""
        SELECT m.id, m.date, m.league_code, t1.name as home, t2.name as away, m.ft_result
        FROM matches m
        JOIN teams t1 ON m.home_team_id = t1.id
        JOIN teams t2 ON m.away_team_id = t2.id
        WHERE DATE(m.date) >= '2026-06-01' AND DATE(m.date) <= '2026-06-30'
        ORDER BY m.date LIMIT 20
    """)
    for m in matches_june:
        print(f"  {m['date']} | {m['league_code']} | {m['home']} vs {m['away']} | Res: {m['ft_result']}")
        
finally:
    db.close()
