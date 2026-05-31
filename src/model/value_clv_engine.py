"""Value & CLV Engine.

Implements overround margin cleaning, value edge classification, CLV tracking,
and database snapshot helpers.
"""

import logging
from datetime import datetime

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────────────
# 1. IMPLIED PROBABILITY ENGINE (MARGIN CLEANING)
# ──────────────────────────────────────────────────────────────────────

def clean_implied_probabilities(h_odds: float, d_odds: float, a_odds: float) -> tuple[float, float, float]:
    """Clean the bookmaker overround margin using the proportional method.

    Returns:
        tuple: (clean_h_prob, clean_d_prob, clean_a_prob) normalized to sum to 1.0.
    """
    if not h_odds or not d_odds or not a_odds or h_odds <= 1.0 or d_odds <= 1.0 or a_odds <= 1.0:
        return 0.333, 0.333, 0.334

    # Raw implied probabilities
    raw_h = 1.0 / h_odds
    raw_d = 1.0 / d_odds
    raw_a = 1.0 / a_odds

    # Overround sum
    overround = raw_h + raw_d + raw_a

    if overround <= 0:
        return 0.333, 0.333, 0.334

    # Cleaned probabilities normalized by overround
    clean_h = round(raw_h / overround, 4)
    clean_d = round(raw_d / overround, 4)
    clean_a = round(raw_a / overround, 4)

    # Normalize to exactly 1.0
    total = clean_h + clean_d + clean_a
    if total != 1.0:
        diff = 1.0 - total
        clean_a = round(clean_a + diff, 4)

    return clean_h, clean_d, clean_a


# ──────────────────────────────────────────────────────────────────────
# 2. VALUE ENGINE (EDGE & CLASSIFICATION)
# ──────────────────────────────────────────────────────────────────────

def calculate_edge(model_prob: float, market_clean_prob: float) -> float:
    """Calculate the edge of our model probability over the cleaned market probability."""
    return round(model_prob - market_clean_prob, 4)


def classify_value(edge: float) -> str:
    """Classify the value of the bet based on model edge percentage."""
    if edge < 0.02:
        return "NO_VALUE"
    elif edge < 0.05:
        return "LOW_VALUE"
    elif edge < 0.08:
        return "MEDIUM_VALUE"
    else:
        return "HIGH_VALUE"


# ──────────────────────────────────────────────────────────────────────
# 3. CLOSING LINE VALUE (CLV) ENGINE
# ──────────────────────────────────────────────────────────────────────

def calculate_clv_pct(opening_odds: float, closing_odds: float) -> float:
    """Calculate Closing Line Value percentage from opening and closing odds."""
    if not opening_odds or not closing_odds or opening_odds <= 0:
        return 0.0
    # Formula: ((closing - opening) / opening) * 100
    clv = ((closing_odds - opening_odds) / opening_odds) * 100.0
    return round(clv, 2)


def classify_clv(clv_pct: float) -> str:
    """Classify CLV strength based on CLV percentage change."""
    if clv_pct >= 10.0:
        return "STRONG_POSITIVE_CLV"
    elif clv_pct >= 2.0:
        return "POSITIVE_CLV"
    elif clv_pct > -2.0:
        return "NEUTRAL_CLV"
    elif clv_pct > -10.0:
        return "NEGATIVE_CLV"
    else:
        return "STRONG_NEGATIVE_CLV"


def calculate_edge_movement(opening_odds: float, closing_odds: float, model_prob: float) -> float:
    """Calculate change in market implied probability from prediction to close."""
    if not opening_odds or not closing_odds or opening_odds <= 0 or closing_odds <= 0:
        return 0.0
    
    # Implied probabilities raw (using clean proportional proxy)
    prob_open = 1.0 / opening_odds
    prob_close = 1.0 / closing_odds
    
    # Edge movement is the change in clean market probability
    movement = prob_close - prob_open
    return round(movement, 4)


# ──────────────────────────────────────────────────────────────────────
# 4. DATABASE HOOKS & STORAGE
# ──────────────────────────────────────────────────────────────────────

def save_odds_snapshot(db, match_id: int, market_type: str, selection: str, bookmaker: str, odds: float) -> None:
    """Save captured market odds snapshot at prediction time."""
    try:
        db.execute("""
            INSERT INTO odds_snapshots (match_id, market_type, selection, bookmaker, odds)
            VALUES (?, ?, ?, ?, ?)
        """, (match_id, market_type, selection, bookmaker, odds))
    except Exception as e:
        logger.error(f"Failed to save odds snapshot: {e}")


def save_closing_odds(db, match_id: int, market_type: str, selection: str, bookmaker: str, odds: float) -> None:
    """Save closing odds and update associated predictions with CLV/Edge statistics."""
    try:
        db.execute("""
            INSERT OR REPLACE INTO closing_odds (match_id, market_type, selection, bookmaker, closing_odds)
            VALUES (?, ?, ?, ?, ?)
        """, (match_id, market_type, selection, bookmaker, odds))

        # 1. Fetch predictions for this match
        predictions = db.fetchall("""
            SELECT id, predicted_result, home_win_prob, draw_prob, away_win_prob
            FROM predictions WHERE match_id = ?
        """, (match_id,))

        if not predictions:
            return

        # 2. Fetch closing odds for 1X2 market to clean implied probabilities
        odds_row = db.fetchone("""
            SELECT 
                (SELECT closing_odds FROM closing_odds WHERE match_id = ? AND market_type = '1X2' AND selection = '1' ORDER BY id DESC LIMIT 1) as h,
                (SELECT closing_odds FROM closing_odds WHERE match_id = ? AND market_type = '1X2' AND selection = 'X' ORDER BY id DESC LIMIT 1) as d,
                (SELECT closing_odds FROM closing_odds WHERE match_id = ? AND market_type = '1X2' AND selection = '2' ORDER BY id DESC LIMIT 1) as a
        """, (match_id, match_id, match_id))

        if odds_row and odds_row["h"] is not None and odds_row["d"] is not None and odds_row["a"] is not None:
            # We have all three closing odds! We can now update the predictions.
            for pred in predictions:
                pred_id = pred["id"]
                result = pred["predicted_result"]
                
                # Map predictions result label to selection format
                sel_norm = "1"
                if result in ("MS 1", "1", "Ev Sahibi", "H"):
                    sel_norm = "1"
                elif result in ("MS X", "X", "Berabere", "D"):
                    sel_norm = "X"
                elif result in ("MS 2", "2", "Deplasman", "A"):
                    sel_norm = "2"

                # Fetch the opening/snapshot odds
                snap = db.fetchone("""
                    SELECT odds FROM odds_snapshots
                    WHERE match_id = ? AND market_type = '1X2' AND selection = ?
                    ORDER BY id DESC LIMIT 1
                """, (match_id, sel_norm))

                if snap:
                    opening_odds = snap["odds"]
                    closing_odds_val = {"1": odds_row["h"], "X": odds_row["d"], "2": odds_row["a"]}.get(sel_norm, odds)
                    clv_pct = calculate_clv_pct(opening_odds, closing_odds_val)
                    clv_class = classify_clv(clv_pct)

                    clean_h, clean_d, clean_a = clean_implied_probabilities(
                        odds_row["h"], odds_row["d"], odds_row["a"]
                    )
                    prob_map = {"1": clean_h, "X": clean_d, "2": clean_a}
                    clean_mkt_prob = prob_map.get(sel_norm, 0.33)

                    model_prob_map = {
                        "1": pred["home_win_prob"],
                        "X": pred["draw_prob"],
                        "2": pred["away_win_prob"]
                    }
                    model_prob = model_prob_map.get(sel_norm, 0.33)
                    value_edge = calculate_edge(model_prob, clean_mkt_prob)
                    value_class = classify_value(value_edge)

                    db.execute("""
                        UPDATE predictions
                        SET prediction_odds = ?,
                            closing_odds = ?,
                            clv_pct = ?,
                            clv_class = ?,
                            value_edge = ?,
                            value_class = ?
                        WHERE id = ?
                    """, (opening_odds, closing_odds_val, clv_pct, clv_class, value_edge, value_class, pred_id))

                    # Trigger the CLV Feedback Adaptive Learning Loop
                    try:
                        from src.model.adaptive_learning import AdaptiveLearningEngine
                        engine = AdaptiveLearningEngine(db)
                        engine.process_clv_feedback(
                            match_id=match_id,
                            selection=sel_norm,
                            model_probability=model_prob,
                            open_odds=opening_odds,
                            close_odds=closing_odds_val,
                            clv_pct=clv_pct
                        )
                    except Exception as loop_err:
                        logger.error(f"Error in CLV feedback learning loop: {loop_err}")
                
    except Exception as e:
        logger.error(f"Failed to save closing odds or update CLV: {e}")
                
    except Exception as e:
        logger.error(f"Failed to save closing odds or update CLV: {e}")


def get_historical_clv(db, league_code: str) -> float:
    """Fetch average historical CLV % for a given league code."""
    if not db:
        return 0.0
    try:
        row = db.fetchone("""
            SELECT AVG(p.clv_pct) as avg_clv
            FROM predictions p
            JOIN matches m ON p.match_id = m.id
            WHERE m.league_code = ? AND p.clv_pct IS NOT NULL
        """, (league_code,))
        if row and row["avg_clv"] is not None:
            return float(row["avg_clv"])
    except Exception as e:
        logger.error(f"Failed to fetch historical CLV for {league_code}: {e}")
    return 0.0
