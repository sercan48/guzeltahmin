"""Prediction Verifier — Bridges predictions with actual match results."""

import logging
from datetime import date
from src.db.base import get_backend
from src.evaluator.coupon_builder import BetType

logger = logging.getLogger(__name__)

def verify_all_pending_predictions():
    """Find predictions in 'predictions' table that aren't verified yet and update them."""
    db = get_backend()
    db.connect()
    
    # Get pending predictions for matches that have ended (ft_result is not null)
    pending = db.fetchall(
        """SELECT p.*, m.ft_home_goals, m.ft_away_goals, m.ft_result as actual_ft_result, 
                  m.ht_home_goals, m.ht_away_goals
           FROM predictions p
           JOIN matches m ON p.match_id = m.id
           WHERE p.actual_result IS NULL 
           AND m.ft_result IS NOT NULL"""
    )
    
    if not pending:
        logger.info("No pending predictions to verify.")
        db.close()
        return 0
    
    updated_count = 0
    for p in pending:
        try:
            # 1. Verification of Main Result (H/D/A)
            actual_ft = p["actual_ft_result"]
            
            # 2. Verification of Specific Bets (Top 1 / Top 2)
            t1_success = _verify_bet(p["top_1_type"], p["top_1_pick"], p["ft_home_goals"], p["ft_away_goals"])
            t2_success = _verify_bet(p["top_2_type"], p["top_2_pick"], p["ft_home_goals"], p["ft_away_goals"]) if p["top_2_pick"] else -1
            
            db.execute(
                """UPDATE predictions 
                   SET actual_result = ?, top_1_success = ?, top_2_success = ?
                   WHERE id = ?""",
                (actual_ft, t1_success, t2_success, p["id"])
            )
            updated_count += 1
        except Exception as e:
            logger.error(f"Error verifying prediction ID {p['id']}: {e}")
            
    db.close()
    logger.info(f"Verified {updated_count} predictions.")
    return updated_count

def _verify_bet(bet_type_val: str, pick: str, h_goals: int, a_goals: int) -> int:
    """Check if a specific bet was successful.

    Supports two systems:
    1. New normalized tags: 1, X, 2, 1X, X2, 12, O25, U25, BTTS_Y, BTTS_N
    2. Legacy Turkish format from BetType enum values
    """
    if h_goals is None or a_goals is None:
        return -1

    total_goals = h_goals + a_goals
    both_scored = h_goals > 0 and a_goals > 0

    # ── New normalized tag resolution (Phase 1) ──────────────────────
    tag = (pick or "").strip()

    # 1X2 tags
    if tag == "1":
        return 1 if h_goals > a_goals else 0
    if tag == "X":
        return 1 if h_goals == a_goals else 0
    if tag == "2":
        return 1 if a_goals > h_goals else 0

    # Double Chance tags
    if tag == "1X":
        return 1 if h_goals >= a_goals else 0
    if tag == "X2":
        return 1 if a_goals >= h_goals else 0
    if tag == "12":
        return 1 if h_goals != a_goals else 0

    # Goals tags
    if tag == "O25":
        return 1 if total_goals > 2.5 else 0
    if tag == "U25":
        return 1 if total_goals < 2.5 else 0

    # BTTS tags
    if tag == "BTTS_Y":
        return 1 if both_scored else 0
    if tag == "BTTS_N":
        return 1 if not both_scored else 0

    # ── Legacy BetType-based resolution (backward compat) ────────────
    # Match Results
    if bet_type_val == BetType.MATCH_RESULT.value:
        actual = "1" if h_goals > a_goals else ("2" if a_goals > h_goals else "X")
        # Handle Turkish pick format: "Ev Sahibi (1)" → compare suffix
        norm_pick = pick
        if "(1)" in pick:
            norm_pick = "1"
        elif "(2)" in pick:
            norm_pick = "2"
        elif "(X)" in pick or "Beraberlik" in pick:
            norm_pick = "X"
        return 1 if norm_pick == actual else 0

    # Over/Under
    if bet_type_val == BetType.OVER_UNDER.value:
        if "Üst" in pick or "Ust" in pick:
            threshold = float(pick.split()[0]) if pick[0].isdigit() else 2.5
            return 1 if total_goals > threshold else 0
        if "Alt" in pick:
            threshold = float(pick.split()[0]) if pick[0].isdigit() else 2.5
            return 1 if total_goals < threshold else 0

    # Both Teams to Score (KG)
    if bet_type_val == BetType.BOTH_TEAMS_SCORE.value:
        if "Var" in pick:
            return 1 if both_scored else 0
        if "Yok" in pick:
            return 1 if not both_scored else 0

    # Double Chance
    if bet_type_val == BetType.DOUBLE_CHANCE.value:
        if "1X" in pick:
            return 1 if h_goals >= a_goals else 0
        if "X2" in pick:
            return 1 if a_goals >= h_goals else 0
        if "12" in pick:
            return 1 if h_goals != a_goals else 0

    # Draw type
    if bet_type_val == BetType.DRAW.value:
        return 1 if h_goals == a_goals else 0

    return 0

def get_accuracy_report():
    """Get summarized statistics of recently verified predictions."""
    db = get_backend()
    db.connect()
    
    stats = db.fetchone(
        """SELECT 
               COUNT(*) as total,
               SUM(CASE WHEN top_1_success = 1 THEN 1 ELSE 0 END) as t1_wins,
               SUM(CASE WHEN top_2_success = 1 THEN 1 ELSE 0 END) as t2_wins,
               SUM(CASE WHEN top_2_success >= 0 THEN 1 ELSE 0 END) as t2_total
           FROM predictions 
           WHERE actual_result IS NOT NULL"""
    )
    
    if stats["total"] == 0:
        db.close()
        return None
        
    report = {
        "total": stats["total"],
        "top_1_rate": round(stats["t1_wins"] / stats["total"] * 100, 1),
        "top_2_rate": round(stats["t2_wins"] / stats["t2_total"] * 100, 1) if stats["t2_total"] > 0 else 0,
        "history": db.fetchall(
            """SELECT p.analysis_date, t1.name as home, t2.name as away, 
                      p.top_1_pick, p.top_1_success, p.top_2_pick, p.top_2_success,
                      m.ft_home_goals, m.ft_away_goals
               FROM predictions p
               JOIN matches m ON p.match_id = m.id
               JOIN teams t1 ON m.home_team_id = t1.id
               JOIN teams t2 ON m.away_team_id = t2.id
               WHERE p.actual_result IS NOT NULL
               ORDER BY p.analysis_date DESC
               LIMIT 20"""
        )
    }
    db.close()
    return report

if __name__ == "__main__":
    verify_all_pending_predictions()
    rep = get_accuracy_report()
    if rep:
        print(f"Verified Results: {rep['total']}")
        print(f"Top 1 Accuracy: {rep['top_1_rate']}%")
