import traceback
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.db.base import get_backend
from src.model.predictor import predict_match

db = get_backend()
db.connect()

match_id = 76505
print(f"=== Replaying Match {match_id} ===")

# Let's fetch the match row from DB to see details
m = db.fetchone("SELECT * FROM matches WHERE id=?", (match_id,))
print(f"Match details in DB: {dict(m) if m else 'None'}")

if m:
    try:
        pred = predict_match(
            db=db,
            home_team_id=m["home_team_id"],
            away_team_id=m["away_team_id"],
            league_code=m["league_code"],
            season=m["season"],
            use_weather=False,
            match_id=m["id"]
        )
        print("Prediction succeeded!")
    except Exception as e:
        print("Prediction failed! Traceback:")
        traceback.print_exc()

db.close()
