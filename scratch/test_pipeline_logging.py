import sys
import logging
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

# Initialize logging to see info-level logs on console
logging.basicConfig(level=logging.INFO, format="%(levelname)s - %(message)s")

from src.db.base import get_backend
from src.model.predictor import predict_match

def main():
    db = get_backend()
    db.connect()

    # Query first 6 upcoming matches from any league
    matches = db.fetchall("""
        SELECT id, home_team_id, away_team_id, league_code, season, date
        FROM matches 
        WHERE ft_result IS NULL
        AND DATE(date) >= '2026-05-29'
        LIMIT 6
    """)

    print("=" * 70)
    print("  PROBABILITY PIPELINE DIAGNOSTIC RUN")
    print("=" * 70)

    for idx, m in enumerate(matches):
        print(f"\nMatch ID {m['id']}: Running predictions...")
        season = m['season'] or "2025-2026"
        try:
            # Recreate build_match_features call to print details
            from src.model.predictor import build_match_features
            features = build_match_features(db, m['home_team_id'], m['away_team_id'], m['league_code'], season)
            
            print("  [All 35 Features]")
            from config.constants import FEATURE_COLUMNS
            for col in FEATURE_COLUMNS:
                val = features.get(col)
                print(f"    * {col:<28}: {val}")
            
            predict_match(
                db=db,
                home_team_id=m['home_team_id'],
                away_team_id=m['away_team_id'],
                league_code=m['league_code'],
                season=season,
                use_weather=True
            )
            # Only print first match details but let it print the rest as well
            pass
        except Exception as e:
            print(f"Error predicting match {m['id']}: {e}")

    db.close()
    print("\n" + "=" * 70)

if __name__ == "__main__":
    main()
