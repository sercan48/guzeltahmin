"""
Ensemble Inference Engine.
Blends Monte Carlo (Poisson) simulation output with non-linear XGBoost output.
"""
from src.model.wc_monte_carlo import TeamStats, run_monte_carlo_simulation
from src.model.wc_xgb_pipeline import prepare_xgb_features, get_xgb_model

def get_top_pick(probs):
    """Returns 'AWAY', 'DRAW', or 'HOME' based on highest probability."""
    # probs = [P(Away), P(Draw), P(Home)]
    idx = probs.index(max(probs))
    if idx == 0: return "AWAY"
    if idx == 1: return "DRAW"
    return "HOME"

def calculate_ensemble_confidence(mc_probs, xgb_probs, base_confidence):
    """
    Rewards agreement and penalizes contradiction between the two models.
    """
    mc_pick = get_top_pick(mc_probs) 
    xgb_pick = get_top_pick(xgb_probs)
    
    ensemble_confidence = base_confidence
    
    if mc_pick == xgb_pick:
        # Both models agree
        if max(mc_probs) > 50.0 and max(xgb_probs) > 50.0:
            ensemble_confidence *= 1.20 # 20% Boost for strong consensus
            status_flag = "🎯 STRONG CONSENSUS"
        else:
            ensemble_confidence *= 1.05 # 5% Boost for soft consensus
            status_flag = "🤝 MODELS AGREE"
    else:
        # Models contradict each other
        divergence = abs(max(mc_probs) - max(xgb_probs))
        
        if divergence > 20.0:
            # Massive contradiction
            ensemble_confidence *= 0.40 # 60% Penalty
            status_flag = "⚠️ HIGH CONTRADICTION (Risk of Upset)"
        else:
            ensemble_confidence *= 0.75 # 25% Penalty
            status_flag = "⚖️ DIVERGING PREDICTIONS"
            
    return min(99.0, max(10.0, ensemble_confidence)), status_flag

def blend_models(mc_probs, xgb_probs, mc_confidence):
    """
    Blends the probabilities dynamically.
    mc_probs and xgb_probs are lists: [Away%, Draw%, Home%]
    """
    mc_weight = mc_confidence / 100.0
    mc_weight = max(0.3, min(0.7, mc_weight)) # Floor 30%, Ceiling 70%
    xgb_weight = 1.0 - mc_weight
    
    blended_probs = []
    for i in range(3):
        blended = (mc_probs[i] * mc_weight) + (xgb_probs[i] * xgb_weight)
        blended_probs.append(blended)
        
    return blended_probs, mc_weight, xgb_weight

def run_ensemble_inference(team_a_stats: TeamStats, team_b_stats: TeamStats):
    """Orchestrator for running both models and returning the final ensemble result."""
    # 1. Run Monte Carlo
    mc_results = run_monte_carlo_simulation(team_a_stats, team_b_stats)
    # Convert to standard format [Away, Draw, Home]
    mc_probs = [mc_results["away_win_prob"], mc_results["draw_prob"], mc_results["home_win_prob"]]
    base_confidence = mc_results["confidence_score"]
    
    # 2. Run XGBoost
    xgb_features = prepare_xgb_features(team_a_stats, team_b_stats)
    xgb_model = get_xgb_model()
    # XGB predict_proba returns [[P(Away), P(Draw), P(Home)]] but in 0-1 scale, we need 0-100 scale
    xgb_probs_raw = xgb_model.predict_proba(xgb_features)[0]
    xgb_probs = [p * 100.0 for p in xgb_probs_raw]
    
    # 3. Dynamic Blending
    blended_probs, mc_wt, xgb_wt = blend_models(mc_probs, xgb_probs, base_confidence)
    
    # 4. Final Confidence Calibrator
    final_confidence, status_flag = calculate_ensemble_confidence(mc_probs, xgb_probs, base_confidence)
    
    return {
        "home_win_prob": round(blended_probs[2], 2),
        "draw_prob": round(blended_probs[1], 2),
        "away_win_prob": round(blended_probs[0], 2),
        "expected_goals_a": mc_results["expected_goals_a"],
        "expected_goals_b": mc_results["expected_goals_b"],
        "confidence_score": round(final_confidence, 2),
        "ensemble_status": status_flag,
        "mc_weight": round(mc_wt * 100, 1),
        "xgb_weight": round(xgb_wt * 100, 1)
    }
