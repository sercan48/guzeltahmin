import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.db.base import get_backend
from src.model.predictor import predict_match

import logging
logging.basicConfig(level=logging.WARNING)  # Mute info logs to see clean output

def main():
    db = get_backend()
    db.connect()
    
    # Query match 72968
    m = db.fetchone("SELECT * FROM matches WHERE id=72968")
    
    print("=" * 60)
    print(f"  TEMPERATURE SCALING TEST: {m['id']} (Almería vs Valladolid)")
    print("=" * 60)
    
    for t in [1.0, 1.15, 1.30]:
        res = predict_match(
            db=db,
            home_team_id=m['home_team_id'],
            away_team_id=m['away_team_id'],
            league_code=m['league_code'],
            season=m['season'] or '2025-2026',
            temperature=t
        )
        print(f"  T = {t:.2f} -> H: {res['home_win_prob']:.4%} | D: {res['draw_prob']:.4%} | A: {res['away_win_prob']:.4%}")
        
    db.close()
    print("=" * 60)

if __name__ == "__main__":
    main()
