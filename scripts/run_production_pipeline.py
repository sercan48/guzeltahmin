"""
Production Pipeline: Fetches today's matches, runs Stacking Ensemble + Omni-Market Scanner,
and saves the final predictions to the database for the Telegram Bot to consume.
"""
import datetime
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.db.base import get_backend
from src.model.predictor import predict_match
from src.evaluator.value_hunter import scan_all_markets

def run_production():
    db = get_backend()
    db.connect()
    
    # 1. Get today's and near-future matches (next 2 days)
    matches = db.fetchall("""
        SELECT id, home_team_id, away_team_id, league_code, season, date
        FROM matches 
        WHERE ft_result IS NULL
        AND DATE(date) >= DATE('now') AND DATE(date) <= DATE('now', '+2 days')
    """)
    print(f"Found {len(matches)} unplayed matches.")
    
    if not matches:
        print("No matches found. Exiting.")
        db.close()
        return
        
    predictions_to_insert = []
    
    # 2. Run Stacking Ensemble ML Predictions & Omni-Market
    for match in matches:
        season = match['season']
        if not season:
            season = "2025-2026"
            
        try:
            pred = predict_match(
                db=db,
                home_team_id=match['home_team_id'],
                away_team_id=match['away_team_id'],
                league_code=match['league_code'],
                season=season,
                use_weather=True
            )
            
            # Omni-Market Scanner
            best_bets = scan_all_markets([pred])
            
            if best_bets:
                best = best_bets[0]
                predicted_result = best['value_outcome']
                conf_score = best['edge'] * 100
            else:
                predicted_result = "SKIP"
                conf_score = 0
                
            if predicted_result != "SKIP":
                odds_snap = pred.get("_odds") or {}
                h_odds = odds_snap.get("h") or odds_snap.get("home_odds")
                d_odds = odds_snap.get("d") or odds_snap.get("draw_odds")
                a_odds = odds_snap.get("a") or odds_snap.get("away_odds")

                # Save odds snapshots to the database
                from src.model.value_clv_engine import save_odds_snapshot
                if h_odds: save_odds_snapshot(db, match['id'], '1X2', '1', 'Ensemble_Mkt', h_odds)
                if d_odds: save_odds_snapshot(db, match['id'], '1X2', 'X', 'Ensemble_Mkt', d_odds)
                if a_odds: save_odds_snapshot(db, match['id'], '1X2', '2', 'Ensemble_Mkt', a_odds)

                pred_res_norm = predicted_result
                if predicted_result in ("MS 1", "1", "Ev Sahibi", "H"):
                    pred_res_norm = "1"
                elif predicted_result in ("MS X", "X", "Berabere", "D"):
                    pred_res_norm = "X"
                elif predicted_result in ("MS 2", "2", "Deplasman", "A"):
                    pred_res_norm = "2"
                
                pred_odds_val = 1.0
                if pred_res_norm == "1": pred_odds_val = h_odds or 1.0
                elif pred_res_norm == "X": pred_odds_val = d_odds or 1.0
                elif pred_res_norm == "2": pred_odds_val = a_odds or 1.0

                value_edge = pred.get("value_edge", 0.0)
                value_class = pred.get("value_class", "NO_VALUE")

                predictions_to_insert.append((
                    match['id'], pred['home_win_prob'], pred['draw_prob'], pred['away_win_prob'],
                    conf_score, 1, value_edge * 100, predicted_result,
                    datetime.datetime.now().isoformat(), 'Ensemble ML',
                    pred_odds_val, value_edge, value_class
                ))
        except Exception as e:
            print(f"Error predicting match {match['id']}: {e}")
            
    # 3. Insert into DB
    for p in predictions_to_insert:
        try:
            db.execute("""
                INSERT INTO predictions (
                    match_id, home_win_prob, draw_prob, away_win_prob, 
                    confidence_score, is_value_bet, value_margin, 
                    predicted_result, analysis_date, model_type,
                    prediction_odds, value_edge, value_class
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, p)
            print(f"Inserted Prediction for Match {p[0]}: {p[7]} (Conf: {p[4]:.1f}%)")
        except Exception:
            # Update if already exists
            try:
                db.execute("""
                    UPDATE predictions 
                    SET predicted_result = ?, confidence_score = ?,
                        home_win_prob = ?, draw_prob = ?, away_win_prob = ?,
                        model_type = ?, analysis_date = ?,
                        prediction_odds = ?, value_edge = ?, value_class = ?
                    WHERE match_id = ?
                """, (p[7], p[4], p[1], p[2], p[3], p[9], p[8], p[10], p[11], p[12], p[0]))
                print(f"Updated Prediction for Match {p[0]}: {p[7]} (Conf: {p[4]:.1f}%)")
            except Exception as ue:
                print(f"Failed to insert/update match {p[0]}: {ue}")
                
    db.close()
    print("Production pipeline completed. Telegram Bot can now send alerts.")

if __name__ == "__main__":
    run_production()
