"""Closing Odds Updater Script.

Retrieves kickoff/closing odds from the synced odds table shortly before kickoff,
stores them in closing_odds snapshots, and triggers CLV and Edge calculations.
"""

import sys
from pathlib import Path
from datetime import datetime

# Add project root to python path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.db.base import get_backend
from src.model.value_clv_engine import save_closing_odds

def update_closing_odds():
    db = get_backend()
    db.connect()
    try:
        # Find matches starting today (or yesterday) that have predictions but no closing odds stored yet
        matches = db.fetchall("""
            SELECT DISTINCT m.id, m.home_team_id, m.away_team_id, m.league_code
            FROM matches m
            JOIN predictions p ON p.match_id = m.id
            WHERE DATE(m.date) >= DATE('now', '-1 day') AND DATE(m.date) <= DATE('now')
            AND m.id NOT IN (SELECT DISTINCT match_id FROM closing_odds)
        """)
        
        print(f"Found {len(matches)} matches to update closing odds.")
        
        updated_count = 0
        for m in matches:
            match_id = m["id"]
            
            # Fetch latest odds from the odds table
            odds_row = db.fetchone("""
                SELECT home_odds, draw_odds, away_odds, bookmaker
                FROM odds
                WHERE match_id = ?
                ORDER BY id DESC LIMIT 1
            """, (match_id,))
            
            if odds_row and odds_row["home_odds"] and odds_row["draw_odds"] and odds_row["away_odds"]:
                h_odds = odds_row["home_odds"]
                d_odds = odds_row["draw_odds"]
                a_odds = odds_row["away_odds"]
                bookmaker = odds_row["bookmaker"] or "Ensemble_Mkt"
                
                # Save closing odds snapshots (this triggers CLV percent recalculation inside value_clv_engine)
                save_closing_odds(db, match_id, '1X2', '1', bookmaker, h_odds)
                save_closing_odds(db, match_id, '1X2', 'X', bookmaker, d_odds)
                save_closing_odds(db, match_id, '1X2', '2', bookmaker, a_odds)
                updated_count += 1
                
        print(f"Closing odds updated successfully for {updated_count} matches.")
    except Exception as e:
        print(f"Error updating closing odds: {e}")
    finally:
        db.close()

if __name__ == "__main__":
    update_closing_odds()
