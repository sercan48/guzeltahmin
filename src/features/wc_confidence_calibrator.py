"""
Confidence Calibrator for World Cup.
Adjusts Monte Carlo Confidence Scores based on Sharp Money (Market Deltas).
"""

def calibrate_confidence(model_prediction: str, confidence_score: float, market_delta: dict):
    """
    Adjusts the final confidence score based on market sentiment.
    Returns the new score and a market_note (flag).
    model_prediction should be one of "HOME_WIN", "DRAW", "AWAY_WIN".
    """
    home_shift = market_delta.get("home_delta", 0.0)
    away_shift = market_delta.get("away_delta", 0.0)
    
    calibrated_score = confidence_score
    is_no_bet = False
    flag_reason = ""
    
    # Define thresholds
    HEAVY_DROP_THRESHOLD = 8.0  # 8% drop in odds is considered sharp money backing
    HEAVY_DRIFT_THRESHOLD = -5.0 # 5% rise in odds is considered market fading
    
    if model_prediction == "HOME_WIN":
        if home_shift >= HEAVY_DROP_THRESHOLD:
            # Market agrees strongly with our model
            calibrated_score = min(99.0, confidence_score * 1.15) 
            flag_reason = "🔥 SHARP MONEY"
        elif home_shift <= HEAVY_DRIFT_THRESHOLD:
            # Market is fading our pick! Dangerous.
            calibrated_score = confidence_score * 0.50 
            is_no_bet = True
            flag_reason = "⚠️ NO BET"
            
    elif model_prediction == "AWAY_WIN":
        if away_shift >= HEAVY_DROP_THRESHOLD:
            calibrated_score = min(99.0, confidence_score * 1.15)
            flag_reason = "🔥 SHARP MONEY"
        elif away_shift <= HEAVY_DRIFT_THRESHOLD:
            calibrated_score = confidence_score * 0.50
            is_no_bet = True
            flag_reason = "⚠️ NO BET"
            
    return {
        "final_confidence": round(calibrated_score, 2),
        "is_no_bet": is_no_bet,
        "market_note": flag_reason
    }
