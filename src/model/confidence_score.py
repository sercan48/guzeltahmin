"""Confidence score calculation based on data quality and prediction certainty."""

from config.constants import CONFIDENCE_PENALTIES, CONFIDENCE_BONUSES


def calculate_confidence(
    prediction: dict,
    features: dict,
    h2h_matches: int = 0,
    has_player_data: bool = True,
    has_odds: bool = True,
    last5_complete: bool = True,
) -> int:
    """Calculate confidence score (0-100) for a prediction.

    Higher = more reliable prediction based on data availability
    and model certainty.
    """
    score = 100

    # Data quality penalties
    if not has_player_data:
        score += CONFIDENCE_PENALTIES["missing_player_data"]

    if not last5_complete:
        score += CONFIDENCE_PENALTIES["missing_last5"]

    if h2h_matches < 3:
        score += CONFIDENCE_PENALTIES["low_h2h_data"]

    if not has_odds:
        score += CONFIDENCE_PENALTIES["missing_odds"]

    # Prediction certainty
    probs = [
        prediction.get("home_win_prob", 0.33),
        prediction.get("draw_prob", 0.33),
        prediction.get("away_win_prob", 0.33),
    ]
    max_prob = max(probs)

    if max_prob < 0.40:
        score += CONFIDENCE_PENALTIES["uncertain_prediction"]

    # Bonuses
    if h2h_matches >= 10:
        score += CONFIDENCE_BONUSES["rich_h2h_data"]

    if has_player_data and has_odds and last5_complete:
        score += CONFIDENCE_BONUSES["complete_data"]

    # Probability spread bonus — clear favorite = higher confidence
    sorted_probs = sorted(probs, reverse=True)
    spread = sorted_probs[0] - sorted_probs[1]
    if spread > 0.25:
        score += 5
    elif spread > 0.15:
        score += 2

    return max(0, min(100, score))


def confidence_label(score: int) -> str:
    """Human-readable confidence label."""
    if score >= 85:
        return "Çok Yüksek"
    elif score >= 70:
        return "Yüksek"
    elif score >= 55:
        return "Orta"
    elif score >= 40:
        return "Düşük"
    else:
        return "Çok Düşük"
