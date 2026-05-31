"""
Three-Tier Scheduling & Inference Logic for World Cup.
Handles 'preliminary', 'night_slip', and 'official' phases.
"""
import logging
from src.db.base import get_backend
from src.features.wc_market_delta import calculate_market_delta
from src.features.wc_confidence_calibrator import calibrate_confidence
from src.model.wc_ensemble_inference import run_ensemble_inference, blend_models, calculate_ensemble_confidence
from src.model.wc_monte_carlo import TeamStats

logger = logging.getLogger(__name__)

# --- MOCKS FOR LINEUP FETCHING ---
def get_expected_lineups(match_id: int):
    """Mocks fetching highest rated players (Expected XIs)."""
    # In reality, queries db for top 11 players per team
    return [{"team_id": 1001, "expected": True}, {"team_id": 1002, "expected": True}]

def fetch_and_get_official_lineups(match_id: int):
    """Mocks fetching official starting XIs via API."""
    return [{"team_id": 1001, "official": True}, {"team_id": 1002, "official": True}]

def extract_team_stats(lineup_data):
    """Mocks extracting TeamStats from lineup data."""
    # Dummy stats for architecture flow
    return TeamStats(elo=1600, att_vs_def_delta=2.5, synergy=5.0, fatigue=1.0)
# ---------------------------------

def update_db_flag(cursor, match_id: int, phase_type: str):
    """Updates the execution flag in the database."""
    flag_col = f"is_{phase_type}_run"
    if phase_type == "night_slip":
        flag_col = "is_night_run"
    
    query = f"UPDATE matches SET {flag_col} = 1 WHERE id = ?"
    cursor.execute(query, (match_id,))

def run_tier_inference(match_id: int, phase_type: str):
    """
    Main entry point for Three-Tier scheduling.
    phase_type: 'preliminary', 'night_slip', 'official'
    """
    valid_phases = ['preliminary', 'night_slip', 'official']
    if phase_type not in valid_phases:
        raise ValueError(f"Invalid phase_type. Must be one of {valid_phases}")
        
    db = get_backend()
    db.connect()
    
    try:
        # 1. Feature Adjustment based on Phase
        if phase_type == 'preliminary':
            # 18:30 UTC+3: Expected Lineups, NO Market Delta
            lineups = get_expected_lineups(match_id)
            market_delta = {"home_delta": 0.0, "away_delta": 0.0} 
            
        elif phase_type == 'night_slip':
            # 23:00 UTC+3: Expected Lineups, WITH Market Delta
            lineups = get_expected_lineups(match_id)
            market_delta = calculate_market_delta(db.cursor, match_id)
            
        elif phase_type == 'official':
            # T-45: Official Lineups, WITH Final Market Delta
            lineups = fetch_and_get_official_lineups(match_id)
            market_delta = calculate_market_delta(db.cursor, match_id)

        # 2. Extract Stats
        team_a_stats = extract_team_stats(lineups[0])
        team_b_stats = extract_team_stats(lineups[1])
        
        # 3. Execute Ensemble Pipeline
        ensemble_results = run_ensemble_inference(team_a_stats, team_b_stats)
        
        # Determine raw pick from blended probabilities
        raw_prediction = "DRAW"
        if ensemble_results["home_win_prob"] > 45:
            raw_prediction = "HOME_WIN"
        elif ensemble_results["away_win_prob"] > 45:
            raw_prediction = "AWAY_WIN"
            
        # 4. Final Sharp Money Calibration
        calibrated = calibrate_confidence(
            model_prediction=raw_prediction, 
            confidence_score=ensemble_results["confidence_score"], 
            market_delta=market_delta
        )
        
        # 5. Update DB Flag
        update_db_flag(db.cursor, match_id, phase_type)
        db.connection.commit()
        
        # Merge outputs
        final_output = {**ensemble_results, **calibrated, "phase": phase_type}
        logger.info(f"Phase '{phase_type}' completed for match {match_id}. Status: {calibrated['market_note']}")
        
        return final_output
        
    except Exception as e:
        logger.error(f"Error in tier inference: {e}")
        db.connection.rollback()
        raise e
    finally:
        db.close()
