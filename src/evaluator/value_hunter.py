"""Value Bet detector — EV-based market ranking engine.

Core formula: EV = (model_prob × market_odds) - 1
  EV > 0    = positive expected value
  EV > 0.05 = strong value bet

Anti-DC-spam: Double Chance gets a -0.15 EV penalty.
It must prove real value, not just high probability at garbage odds.
"""

from config.constants import VALUE_BET_THRESHOLDS

# EV penalties and bonuses for market selection
DC_EV_PENALTY = -0.15
DIVERSITY_BONUS = 0.03
MIN_EV_THRESHOLD = 0.02

# Normalized tags for DB storage (predicted_result column)
TAG_MAP = {
    "MS 1": "1",
    "MS X": "X",
    "MS 2": "2",
    "1X Çifte Şans": "1X",
    "X2 Çifte Şans": "X2",
    "12 Çifte Şans": "12",
    "2.5 ÜST": "O25",
    "2.5 ALT": "U25",
    "KG VAR": "BTTS_Y",
    "KG YOK": "BTTS_N",
}


def probability_to_odds(probability: float) -> float:
    """Convert probability to decimal odds."""
    if probability <= 0:
        return 100.0
    return round(1.0 / probability, 2)


def calculate_ev(model_prob: float, market_odds: float) -> float:
    """Expected Value: how much profit per unit stake long-term.

    EV = (prob * odds) - 1
    EV of 0.10 means 10% expected profit per bet.
    """
    if model_prob <= 0 or market_odds <= 1.0:
        return -1.0
    return (model_prob * market_odds) - 1.0


def kelly_fraction(model_prob: float, market_odds: float,
                   max_fraction: float = 0.05) -> float:
    """Kelly Criterion for optimal stake sizing.

    kelly = (prob * odds - 1) / (odds - 1)
    Capped at max_fraction to avoid over-betting.
    """
    if market_odds <= 1.0 or model_prob <= 0:
        return 0.0
    k = (model_prob * market_odds - 1) / (market_odds - 1)
    return round(max(0, min(k, max_fraction)), 4)


def detect_value_bet(
    model_prob: float,
    market_odds: float,
    confidence_score: int,
) -> dict:
    """Check if a bet offers value based on model vs market comparison."""
    model_odds = probability_to_odds(model_prob)

    if model_odds <= 0 or market_odds <= 0:
        return _no_value(model_odds)

    value_margin = ((market_odds - model_odds) / model_odds) * 100
    market_prob = 1.0 / market_odds
    edge = model_prob - market_prob
    ev = calculate_ev(model_prob, market_odds)

    banko_thresh = VALUE_BET_THRESHOLDS["banko"]
    value_thresh = VALUE_BET_THRESHOLDS["value"]

    is_banko = (
        value_margin >= banko_thresh["margin"]
        and confidence_score >= banko_thresh["confidence"]
    )
    is_value = (
        value_margin >= value_thresh["margin"]
        and confidence_score >= value_thresh["confidence"]
    )

    if is_banko:
        label = "DEĞERLI BANKO 🎯"
    elif is_value:
        label = "DEĞERLI ✅"
    else:
        label = "DEĞERSIZ ❌"

    return {
        "is_value": is_value or is_banko,
        "is_banko": is_banko,
        "model_odds": model_odds,
        "value_margin": round(value_margin, 2),
        "edge": round(edge, 4),
        "ev": round(ev, 4),
        "kelly": kelly_fraction(model_prob, market_odds),
        "label": label,
    }


def scan_all_markets(predictions: list[dict]) -> list[dict]:
    """EV-based Omni-Market Scanner.

    For each match, evaluates all viable markets and ranks by Expected Value.
    Anti-DC-spam: DC picks receive a -0.15 EV penalty.
    Only picks with EV >= MIN_EV_THRESHOLD survive.
    """
    best_bets = []

    for pred in predictions:
        p_1 = pred.get("home_win_prob", 0)
        p_x = pred.get("draw_prob", 0)
        p_2 = pred.get("away_win_prob", 0)

        p_1x = p_1 + p_x
        p_x2 = p_x + p_2
        p_12 = p_1 + p_2

        p_over25 = pred.get("over_25_prob", pred.get("over25_prob", 0))
        p_under25 = pred.get("under_25_prob", pred.get("under25_prob", 0))
        p_btts_yes = pred.get("btts_yes_prob", pred.get("btts_prob", 0))

        # Get actual market odds if available
        odds_data = pred.get("_odds") or pred.get("odds") or {}
        has_real_odds = bool(odds_data)

        h_odds = odds_data.get("h", probability_to_odds(p_1) if p_1 > 0 else 2.5)
        d_odds = odds_data.get("d", probability_to_odds(p_x) if p_x > 0 else 3.3)
        a_odds = odds_data.get("a", probability_to_odds(p_2) if p_2 > 0 else 3.0)
        o25_odds = odds_data.get("o25", probability_to_odds(p_over25) if p_over25 > 0 else 1.9)
        u25_odds = odds_data.get("u25", probability_to_odds(p_under25) if p_under25 > 0 else 1.9)

        # Self-referential penalty: when odds are derived from model probs,
        # EV is mathematically ~0. Apply penalty to prevent phantom value signals.
        SELF_REF_PENALTY = -0.08 if not has_real_odds else 0.0

        # Estimate DC odds from 1X2 odds (realistic: ~8% margin)
        dc_1x_odds = round(1.0 / (1.0/h_odds + 1.0/d_odds) * 1.08, 2) if h_odds and d_odds else 1.15
        dc_x2_odds = round(1.0 / (1.0/d_odds + 1.0/a_odds) * 1.08, 2) if d_odds and a_odds else 1.15
        dc_12_odds = round(1.0 / (1.0/h_odds + 1.0/a_odds) * 1.08, 2) if h_odds and a_odds else 1.15

        # BTTS odds estimation
        btts_odds = probability_to_odds(p_btts_yes) * 1.08 if p_btts_yes > 0 else 1.8

        markets = [
            {"outcome": "MS 1", "prob": p_1, "odds": h_odds,
             "threshold": 0.50, "is_dc": False, "market_type": "1X2"},
            {"outcome": "MS X", "prob": p_x, "odds": d_odds,
             "threshold": 0.28, "is_dc": False, "market_type": "1X2"},
            {"outcome": "MS 2", "prob": p_2, "odds": a_odds,
             "threshold": 0.50, "is_dc": False, "market_type": "1X2"},
            {"outcome": "1X Çifte Şans", "prob": p_1x, "odds": dc_1x_odds,
             "threshold": 0.82, "is_dc": True, "market_type": "DC"},
            {"outcome": "X2 Çifte Şans", "prob": p_x2, "odds": dc_x2_odds,
             "threshold": 0.82, "is_dc": True, "market_type": "DC"},
            {"outcome": "12 Çifte Şans", "prob": p_12, "odds": dc_12_odds,
             "threshold": 0.82, "is_dc": True, "market_type": "DC"},
            {"outcome": "2.5 ÜST", "prob": p_over25, "odds": o25_odds,
             "threshold": 0.55, "is_dc": False, "market_type": "GOALS"},
            {"outcome": "2.5 ALT", "prob": p_under25, "odds": u25_odds,
             "threshold": 0.55, "is_dc": False, "market_type": "GOALS"},
            {"outcome": "KG VAR", "prob": p_btts_yes, "odds": btts_odds,
             "threshold": 0.58, "is_dc": False, "market_type": "BTTS"},
        ]

        # Calculate EV for each market and filter
        scored_markets = []
        for m in markets:
            if m["prob"] < m["threshold"]:
                continue

            ev = calculate_ev(m["prob"], m["odds"])

            # Anti-DC-spam penalty
            if m["is_dc"]:
                ev += DC_EV_PENALTY

            # Self-referential odds penalty (no real market data)
            ev += SELF_REF_PENALTY

            if ev < MIN_EV_THRESHOLD:
                continue

            m["ev"] = round(ev, 4)
            m["kelly"] = kelly_fraction(m["prob"], m["odds"])
            m["has_real_odds"] = has_real_odds
            scored_markets.append(m)

        if not scored_markets:
            continue

        # Sort by EV descending — true value, not just probability
        scored_markets.sort(key=lambda x: x["ev"], reverse=True)
        best = scored_markets[0]

        # Normalized tag for DB predicted_result
        value_tag = TAG_MAP.get(best["outcome"], best["outcome"])

        # Primary vs Secondary weight comparison
        primary_weight = _compute_primary_weight(pred, best)

        best_bets.append({
            **pred,
            "value_outcome": best["outcome"],
            "value_tag": value_tag,
            "value_market_type": best["market_type"],
            "value_ev": best["ev"],
            "value_margin": best["ev"] * 100,
            "value_kelly": best["kelly"],
            "value_label": _ev_label(best["ev"]),
            "primary_weight": primary_weight,
            "edge": best["prob"],
            "model_odds": probability_to_odds(best["prob"]),
            "market_odds": best["odds"],
            "is_banko": best["ev"] >= 0.10 and best["prob"] >= 0.60,
            "is_dc": best["is_dc"],
            "all_viable_markets": scored_markets[:3],
        })

    return sorted(best_bets, key=lambda x: x["value_ev"], reverse=True)


def _ev_label(ev: float) -> str:
    if ev >= 0.15:
        return "YÜKSEK DEĞER 🔥"
    elif ev >= 0.08:
        return "GÜÇLÜ DEĞER ✅"
    elif ev >= 0.02:
        return "DEĞERLI 👍"
    return "DEĞERSIZ ❌"


def _compute_primary_weight(pred: dict, best_market: dict) -> float:
    """Compare model's raw 1X2 prediction vs value scanner's best market.

    Returns a weight [0..1] indicating how much to trust the value pick
    over the raw model prediction. Higher = value pick is clearly better.
    """
    h, d, a = pred.get("home_win_prob", 0), pred.get("draw_prob", 0), pred.get("away_win_prob", 0)
    raw_pred = pred.get("predicted_result", "")

    # Raw model confidence for its own prediction
    raw_conf_map = {"H": h, "D": d, "A": a}
    raw_confidence = raw_conf_map.get(raw_pred, 0)

    value_ev = best_market.get("ev", 0)
    value_prob = best_market.get("prob", 0)

    # Score: EV advantage + probability advantage (capped at 1.0)
    ev_advantage = max(0, value_ev) * 3.0  # Scale EV into [0..~0.6]
    prob_advantage = max(0, value_prob - raw_confidence) * 2.0
    weight = min(1.0, 0.3 + ev_advantage + prob_advantage)

    return round(weight, 3)


def compare_primary_secondary(pred: dict) -> dict:
    """Public API: decide which prediction should lead the betting slip.

    Returns:
        winner: 'primary' (raw model 1X2) or 'secondary' (value scanner best market)
        reason: human-readable explanation
        tag: the market tag to use in predicted_result
    """
    raw_pred = pred.get("predicted_result", "")
    value_tag = pred.get("value_tag", "")
    primary_weight = pred.get("primary_weight", 0.5)

    # If value scanner found no alternative or weight is low, keep raw
    if not value_tag or primary_weight < 0.45:
        return {
            "winner": "primary",
            "reason": f"Model tahmin ({raw_pred}) daha güvenilir (weight={primary_weight:.2f})",
            "tag": raw_pred,
        }

    return {
        "winner": "secondary",
        "reason": f"Value tarama ({value_tag}) daha yüksek EV (weight={primary_weight:.2f})",
        "tag": value_tag,
    }


def _no_value(model_odds: float) -> dict:
    return {
        "is_value": False,
        "is_banko": False,
        "model_odds": model_odds,
        "value_margin": 0.0,
        "edge": 0.0,
        "ev": -1.0,
        "kelly": 0.0,
        "label": "DEĞERSIZ ❌",
    }
