import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.db.base import get_backend
from src.model.predictor import predict_match
from src.evaluator.value_hunter import scan_all_markets

db = get_backend()
db.connect()

try:
    # Let's find one of the new matches
    match = db.fetchone("""
        SELECT m.id, m.home_team_id, m.away_team_id, m.league_code, m.season, m.date,
               t1.name as home, t2.name as away
        FROM matches m
        JOIN teams t1 ON m.home_team_id = t1.id
        JOIN teams t2 ON m.away_team_id = t2.id
        WHERE m.league_code = 'NORWAY_ELITESERIEN' AND m.date >= '2026-01-01' AND m.ft_result IS NULL
        LIMIT 1
    """)
    if match:
        print("Evaluating match:", match['home'], "vs", match['away'])
        pred = predict_match(
            db=db,
            home_team_id=match['home_team_id'],
            away_team_id=match['away_team_id'],
            league_code=match['league_code'],
            season=match['season'],
            use_weather=True,
            home_team_name=match['home']
        )
        print("\nPrediction keys:", pred.keys())
        print("Home win prob:", pred.get("home_win_prob"))
        print("Draw prob:", pred.get("draw_prob"))
        print("Away win prob:", pred.get("away_win_prob"))
        
        # Check odds in DB for this match
        odds = db.fetchall("SELECT * FROM odds WHERE match_id = ?", (match['id'],))
        print(f"\nOdds in DB: {len(odds)}")
        for o in odds:
            print(dict(o))
            
        # Try scanning markets
        best_bets = scan_all_markets([pred])
        print(f"\nBest bets scanned: {best_bets}")
    else:
        print("No unplayed Norway matches in 2026 found.")
finally:
    db.close()
