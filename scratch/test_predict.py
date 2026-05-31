import sys
from pathlib import Path
sys.path.insert(0, str(Path('.').absolute()))

from src.db.base import get_backend
from src.model.predictor import predict_match

db = get_backend()
db.connect()

matches = db.fetchall("SELECT id, home_team_id, away_team_id, league_code, season FROM matches WHERE ft_result IS NULL LIMIT 1")
print("Matches:", matches)
if matches:
    match = matches[0]
    season = match['season'] or "2025-2026"
    try:
        pred = predict_match(db, match['home_team_id'], match['away_team_id'], match['league_code'], season)
        print("Prediction successful:", pred['predicted_result'])
    except Exception as e:
        print("Prediction failed:", e)

db.close()
