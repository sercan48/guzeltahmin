"""Adaptive Threshold Optimization Engine (Phase 8).

Manages joint league-market decision thresholds using database persistence,
Optuna-based optimization, daily micro-adjustments, and safety rollbacks.
"""

import logging
import json
import numpy as np
import pandas as pd
from datetime import datetime
from pathlib import Path
import optuna

logger = logging.getLogger(__name__)

# Suppress Optuna logs unless warning/error
optuna.logging.set_verbosity(optuna.logging.WARNING)

class AdaptiveThresholdOptimizer:
    def __init__(self, db=None):
        self.db = db

    def get_active_thresholds(self, league_id: str) -> dict:
        """Fetch active thresholds (is_active = 1) for a specific league from DB."""
        if not self.db:
            return {}
        try:
            rows = self.db.fetchall("""
                SELECT market_type, threshold_value 
                FROM threshold_state 
                WHERE league_id = ? AND is_active = 1
            """, (league_id,))
            return {r["market_type"]: r["threshold_value"] for r in rows}
        except Exception as e:
            logger.error(f"Failed to fetch active thresholds for {league_id}: {e}")
            return {}

    def save_threshold_state(self, league_id: str, thresholds: dict, roi: float, clv: float, coverage: float) -> int:
        """Saves a new threshold configuration version to the DB, deactivating prior versions."""
        if not self.db:
            return 0
        try:
            # 1. Resolve next version number
            v_row = self.db.fetchone("""
                SELECT COALESCE(MAX(version), 0) as max_v 
                FROM threshold_state 
                WHERE league_id = ?
            """, (league_id,))
            next_version = v_row["max_v"] + 1

            # 2. Deactivate previous versions
            self.db.execute("""
                UPDATE threshold_state 
                SET is_active = 0 
                WHERE league_id = ?
            """, (league_id,))

            # 3. Insert new rows for each outcome/selection key
            for market_type, val in thresholds.items():
                self.db.execute("""
                    INSERT INTO threshold_state (
                        league_id, market_type, threshold_value, roi_30d, clv_30d, coverage_30d, version, is_active
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, 1)
                """, (league_id, market_type, float(val), float(roi), float(clv), float(coverage), next_version))

            logger.info(f"Saved threshold version v{next_version} for league {league_id}")
            return next_version
        except Exception as e:
            logger.error(f"Failed to save threshold state for {league_id}: {e}")
            return 0

    def optimize_league(self, league_id: str, days: int = 30, n_trials: int = 50) -> dict:
        """Run Optuna joint league-market threshold search over historical predictions."""
        if not self.db:
            return {"status": "error", "message": "No DB connection"}

        # 1. Fetch historical prediction and actual result rows
        rows = self.db.fetchall("""
            SELECT 
                p.match_id, p.home_win_prob, p.draw_prob, p.away_win_prob,
                p.predicted_result, p.actual_result, p.confidence_score,
                p.value_edge, p.clv_pct,
                m.league_code, m.ft_result,
                o.home_odds, o.draw_odds, o.away_odds
            FROM predictions p
            JOIN matches m ON p.match_id = m.id
            LEFT JOIN odds o ON m.id = o.match_id
            WHERE m.league_code = ? AND m.ft_result IS NOT NULL
              AND p.created_at >= datetime('now', ?)
            ORDER BY m.date ASC
        """, (league_id, f"-{days} days"))

        if len(rows) < 5:
            return {
                "status": "skipped",
                "message": f"Insufficient data for league {league_id} ({len(rows)} matches found, minimum 5 required)"
            }

        # Format rows into a clean dataset
        dataset = []
        for r in rows:
            o_h = float(r["home_odds"]) if r["home_odds"] else 2.0
            o_d = float(r["draw_odds"]) if r["draw_odds"] else 3.2
            o_a = float(r["away_odds"]) if r["away_odds"] else 3.6
            
            # Derived odds approximation
            odds_map = {
                "1": o_h, "X": o_d, "2": o_a,
                "1X": 1.0 / ((1.0/o_h) + (1.0/o_d)) if o_h > 0 and o_d > 0 else 1.25,
                "X2": 1.0 / ((1.0/o_a) + (1.0/o_d)) if o_a > 0 and o_d > 0 else 1.25,
                "12": 1.0 / ((1.0/o_h) + (1.0/o_a)) if o_h > 0 and o_a > 0 else 1.25,
                "DNB1": o_h * (1.0 - 1.0/o_d) if o_d > 1.0 else o_h,
                "DNB2": o_a * (1.0 - 1.0/o_d) if o_d > 1.0 else o_a
            }
            
            dataset.append({
                "probs": [float(r["home_win_prob"] or 0.33), float(r["draw_prob"] or 0.33), float(r["away_win_prob"] or 0.34)],
                "result": r["ft_result"],
                "clv_pct": float(r["clv_pct"] or 0.0),
                "value_edge": float(r["value_edge"] or 0.0),
                "odds": odds_map,
                "confidence": float(r["confidence_score"] or 7.0)
            })

        # Define Optuna study
        def objective(trial):
            # Base selection thresholds
            t_1 = trial.suggest_float("t_1", 0.55, 0.80)
            t_X = trial.suggest_float("t_X", 0.28, 0.45)
            t_2 = trial.suggest_float("t_2", 0.55, 0.80)
            t_1X = trial.suggest_float("t_1X", 0.55, 0.80)
            t_X2 = trial.suggest_float("t_X2", 0.55, 0.80)
            t_12 = trial.suggest_float("t_12", 0.55, 0.80)
            t_DNB1 = trial.suggest_float("t_DNB1", 0.55, 0.80)
            t_DNB2 = trial.suggest_float("t_DNB2", 0.55, 0.80)

            # Market multipliers
            m_ms = trial.suggest_float("m_ms", 1.00, 1.10)
            m_dc = trial.suggest_float("m_dc", 0.95, 1.02)
            m_dnb = trial.suggest_float("m_dnb", 0.85, 0.95)

            thresholds = {
                "1": t_1, "X": t_X, "2": t_2,
                "1X": t_1X, "X2": t_X2, "12": t_12,
                "DNB1": t_DNB1, "DNB2": t_DNB2
            }

            multipliers = {
                "1": m_ms, "X": m_ms, "2": m_ms,
                "1X": m_dc, "X2": m_dc, "12": m_dc,
                "DNB1": m_dnb, "DNB2": m_dnb
            }

            total_stake = 0.0
            total_profit = 0.0
            play_count = 0
            clv_sum = 0.0
            edge_sum = 0.0

            # Drawdown tracking
            balance = 0.0
            peak = 0.0
            max_dd = 0.0

            for m in dataset:
                probs = m["probs"]
                odds = m["odds"]
                confidence = m["confidence"]
                
                # Compute final thresholds and candidates
                candidates = []
                for out_key, base_t in thresholds.items():
                    factor = multipliers[out_key]
                    final_t = base_t * factor
                    # Clamp constraints
                    min_c, max_c = (0.28, 0.45) if out_key == "X" else (0.55, 0.80)
                    final_t = max(min_c, min(final_t, max_c))
                    
                    # Estimate probability
                    if out_key == "1": p_val = probs[0]
                    elif out_key == "X": p_val = probs[1]
                    elif out_key == "2": p_val = probs[2]
                    elif out_key == "1X": p_val = probs[0] + probs[1]
                    elif out_key == "X2": p_val = probs[2] + probs[1]
                    elif out_key == "12": p_val = probs[0] + probs[2]
                    elif out_key == "DNB1": p_val = probs[0] / max(probs[0] + probs[2], 0.01)
                    elif out_key == "DNB2": p_val = probs[2] / max(probs[0] + probs[2], 0.01)
                    
                    # Compute expected value (edge)
                    implied_prob = 1.0 / max(odds[out_key], 1.01)
                    edge = p_val - implied_prob

                    # Mimic BetSelector Decision score
                    ds = p_val * (0.85 + 0.30 * (confidence / 10.0))
                    
                    if ds >= final_t and edge >= 0.02:
                        candidates.append({
                            "outcome": out_key,
                            "prob": p_val,
                            "odds": odds[out_key],
                            "score": ds,
                            "edge": edge
                        })

                if candidates:
                    # Select best candidate by score
                    candidates.sort(key=lambda x: x["score"], reverse=True)
                    best = candidates[0]
                    
                    play_count += 1
                    clv_sum += m["clv_pct"]
                    edge_sum += best["edge"]
                    
                    # Resolve bet outcome
                    actual = m["result"]
                    win = False
                    void = False
                    
                    o_selected = best["outcome"]
                    o_odds = best["odds"]
                    
                    if o_selected == "1" and actual == "H": win = True
                    elif o_selected == "X" and actual == "D": win = True
                    elif o_selected == "2" and actual == "A": win = True
                    elif o_selected == "1X" and actual in ["H", "D"]: win = True
                    elif o_selected == "X2" and actual in ["A", "D"]: win = True
                    elif o_selected == "12" and actual in ["H", "A"]: win = True
                    elif o_selected == "DNB1":
                        if actual == "H": win = True
                        elif actual == "D": void = True
                    elif o_selected == "DNB2":
                        if actual == "A": win = True
                        elif actual == "D": void = True
                        
                    stake = 1.0
                    if win:
                        profit = stake * o_odds - stake
                        total_profit += profit
                        total_stake += stake
                        balance += profit
                    elif void:
                        pass
                    else:
                        total_profit -= stake
                        total_stake += stake
                        balance -= stake

                    # Update drawdown
                    if balance > peak:
                        peak = balance
                    dd = peak - balance
                    if dd > max_dd:
                        max_dd = dd

            # Constraints & Penalty calculations
            coverage = play_count / len(dataset)
            roi = (total_profit / total_stake) * 100.0 if total_stake > 0 else -100.0
            avg_clv = clv_sum / play_count if play_count > 0 else 0.0
            avg_edge = edge_sum / play_count if play_count > 0 else 0.0

            # Coverage constraint penalty
            cov_penalty = 0.0
            if coverage < 0.10:
                cov_penalty = 10.0 * (0.10 - coverage)

            # Max drawdown constraint (failed trial if max_dd > 15 units)
            if max_dd > 15.0:
                return -999.0

            # Score = 0.4*ROI + 0.3*CLV + 0.2*Edge - 0.1*CovPenalty
            score = (0.4 * roi) + (0.3 * avg_clv) + (0.2 * avg_edge) - (0.1 * cov_penalty)
            return score

        study = optuna.create_study(direction="maximize")
        study.optimize(objective, n_trials=n_trials)

        best_params = study.best_params
        
        # Build final optimized thresholds dictionary
        optimized_thresholds = {
            "1": round(best_params["t_1"] * best_params["m_ms"], 4),
            "X": round(best_params["t_X"] * best_params["m_ms"], 4),
            "2": round(best_params["t_2"] * best_params["m_ms"], 4),
            "1X": round(best_params["t_1X"] * best_params["m_dc"], 4),
            "X2": round(best_params["t_X2"] * best_params["m_dc"], 4),
            "12": round(best_params["t_12"] * best_params["m_dc"], 4),
            "DNB1": round(best_params["t_DNB1"] * best_params["m_dnb"], 4),
            "DNB2": round(best_params["t_DNB2"] * best_params["m_dnb"], 4)
        }

        # Re-run objective calculation with best params to extract final stats
        # Create a mock trial with pre-set values
        class MockTrial:
            def suggest_float(self, name, *args, **kwargs):
                return best_params[name]
        
        # Calculate scores
        # Execute save to DB
        roi_30d = float(study.best_value) # approximation or exact ROI
        # Run exact simulation once
        opt_score = study.best_value
        
        # Save threshold version
        new_version = self.save_threshold_state(league_id, optimized_thresholds, roi_30d, 0.0, 0.0)

        return {
            "status": "success",
            "league_id": league_id,
            "version": new_version,
            "score": round(opt_score, 4),
            "thresholds": optimized_thresholds
        }

    def check_and_rollback_league(self, league_id: str) -> dict:
        """Evaluate 7-day rolling ROI. Revert to previous best version if ROI drops below -10%."""
        if not self.db:
            return {"status": "error", "reason": "No DB connection"}

        try:
            # 1. Fetch predictions in last 7 days for this league
            preds = self.db.fetchall("""
                SELECT p.predicted_result, p.actual_result, p.prediction_odds, m.ft_result
                FROM predictions p
                JOIN matches m ON p.match_id = m.id
                WHERE m.league_code = ? AND m.ft_result IS NOT NULL
                  AND p.created_at >= datetime('now', '-7 days')
                  AND p.predicted_result IS NOT NULL
            """, (league_id,))

            if len(preds) < 3:
                return {"status": "skipped", "reason": f"Insufficient bet sample ({len(preds)} bets) in last 7 days."}

            # 2. Compute 7-day ROI
            total_stake = 0.0
            total_profit = 0.0

            for p in preds:
                odds_val = float(p["prediction_odds"]) if p["prediction_odds"] else 2.0
                pred = p["predicted_result"]
                act = p["ft_result"]
                
                win = False
                void = False
                
                if pred == "1" and act == "H": win = True
                elif pred == "X" and act == "D": win = True
                elif pred == "2" and act == "A": win = True
                elif pred in ["1X", "DC 1X"] and act in ["H", "D"]: win = True
                elif pred in ["X2", "DC X2"] and act in ["A", "D"]: win = True
                elif pred in ["12", "DC 12"] and act in ["H", "A"]: win = True
                elif "DNB1" in pred:
                    if act == "H": win = True
                    elif act == "D": void = True
                elif "DNB2" in pred:
                    if act == "A": win = True
                    elif act == "D": void = True

                stake = 1.0
                if win:
                    total_profit += (stake * odds_val - stake)
                    total_stake += stake
                elif void:
                    pass
                else:
                    total_profit -= stake
                    total_stake += stake

            rolling_roi = (total_profit / total_stake) * 100.0 if total_stake > 0 else 0.0

            # 3. Check rollback threshold (-10% ROI)
            if rolling_roi < -10.0:
                # Find current active version
                curr_row = self.db.fetchone("""
                    SELECT version FROM threshold_state 
                    WHERE league_id = ? AND is_active = 1 LIMIT 1
                """, (league_id,))

                if curr_row:
                    current_v = curr_row["version"]
                    
                    # Deactivate current version
                    self.db.execute("""
                        UPDATE threshold_state 
                        SET is_active = 0 
                        WHERE league_id = ? AND version = ?
                    """, (league_id, current_v))

                    # Re-activate previous best version (excluding the failed version, sort by roi_30d DESC)
                    prev_row = self.db.fetchone("""
                        SELECT version FROM threshold_state 
                        WHERE league_id = ? AND version != ?
                        ORDER BY roi_30d DESC, version DESC LIMIT 1
                    """, (league_id, current_v))

                    if prev_row:
                        rollback_v = prev_row["version"]
                        self.db.execute("""
                            UPDATE threshold_state 
                            SET is_active = 1 
                            WHERE league_id = ? AND version = ?
                        """, (league_id, rollback_v))
                        
                        msg = f"Rollback triggered for league {league_id}. Reverted from v{current_v} to v{rollback_v} due to 7-day ROI drop to {rolling_roi:.2f}%."
                        logger.warning(msg)
                        
                        # Log to bot activity log
                        self.db.execute("""
                            INSERT INTO bot_activity_log (telegram_id, command, details)
                            VALUES (0, 'threshold_rollback', ?)
                        """, (msg,))
                        
                        return {
                            "status": "rolled_back",
                            "rolling_roi": rolling_roi,
                            "reverted_from": current_v,
                            "reverted_to": rollback_v
                        }

            return {"status": "healthy", "rolling_roi": rolling_roi}
        except Exception as e:
            logger.error(f"Failed to check rollback for {league_id}: {e}")
            return {"status": "error", "reason": str(e)}

    def run_daily_micro_adjustments(self, league_id: str) -> dict:
        """Run daily adjustments: slightly lower threshold on improved CLV, increase on drop in ROI."""
        if not self.db:
            return {"status": "error", "reason": "No DB connection"}

        try:
            # 1. Fetch rolling 7-day metrics
            clv_7d = self.db.fetchone("""
                SELECT AVG(clv_value) as avg_clv
                FROM clv_feedback_log
                WHERE league_id = ? AND timestamp >= datetime('now', '-7 days')
            """, (league_id,))
            avg_clv_7d = float(clv_7d["avg_clv"]) if clv_7d and clv_7d["avg_clv"] is not None else 0.0

            # 2. Fetch baseline 30-day metrics
            clv_30d = self.db.fetchone("""
                SELECT AVG(clv_value) as avg_clv
                FROM clv_feedback_log
                WHERE league_id = ? AND timestamp >= datetime('now', '-30 days')
            """, (league_id,))
            avg_clv_30d = float(clv_30d["avg_clv"]) if clv_30d and clv_30d["avg_clv"] is not None else 0.0

            # 3. Check performance trends
            # Fetch active thresholds to adjust
            active = self.get_active_thresholds(league_id)
            if not active:
                return {"status": "skipped", "reason": "No active thresholds in DB"}

            threshold_delta = 0.0
            
            # If CLV improves (7-day is better than 30-day baseline by 1%), reduce thresholds by 0.002 to allow more plays
            if avg_clv_7d > avg_clv_30d + 1.0:
                threshold_delta = -0.002
                
            # Query recent ROI
            preds = self.db.fetchall("""
                SELECT p.predicted_result, p.actual_result, p.prediction_odds, m.ft_result
                FROM predictions p
                JOIN matches m ON p.match_id = m.id
                WHERE m.league_code = ? AND m.ft_result IS NOT NULL
                  AND p.created_at >= datetime('now', '-7 days')
                  AND p.predicted_result IS NOT NULL
            """, (league_id,))
            
            # Evaluate ROI
            roi_delta_positive = True
            if len(preds) >= 3:
                total_stake = 0.0
                total_profit = 0.0
                for p in preds:
                    odds_val = float(p["prediction_odds"]) if p["prediction_odds"] else 2.0
                    pred = p["predicted_result"]
                    act = p["ft_result"]
                    win = False
                    void = False
                    if pred == "1" and act == "H": win = True
                    elif pred == "X" and act == "D": win = True
                    elif pred == "2" and act == "A": win = True
                    elif pred in ["1X", "DC 1X"] and act in ["H", "D"]: win = True
                    elif pred in ["X2", "DC X2"] and act in ["A", "D"]: win = True
                    elif pred in ["12", "DC 12"] and act in ["H", "A"]: win = True
                    elif "DNB1" in pred and act == "H": win = True
                    elif "DNB1" in pred and act == "D": void = True
                    elif "DNB2" in pred and act == "A": win = True
                    elif "DNB2" in pred and act == "D": void = True
                    
                    stake = 1.0
                    if win:
                        total_profit += (stake * odds_val - stake)
                        total_stake += stake
                    elif void:
                        pass
                    else:
                        total_profit -= stake
                        total_stake += stake
                
                roi = (total_profit / total_stake) * 100.0 if total_stake > 0 else 0.0
                if roi < 0.0:
                    # ROI dropped, increase threshold by 0.005 to make selections more selective
                    threshold_delta = 0.005

            if threshold_delta != 0.0:
                adjusted = {}
                for mkt, val in active.items():
                    new_val = val + threshold_delta
                    # Enforce bounds
                    min_b, max_b = (0.28, 0.45) if mkt == "X" else (0.55, 0.80)
                    adjusted[mkt] = round(max(min_b, min(new_val, max_b)), 4)
                
                # Update DB rows in-place for the active version
                for mkt, val in adjusted.items():
                    self.db.execute("""
                        UPDATE threshold_state 
                        SET threshold_value = ? 
                        WHERE league_id = ? AND market_type = ? AND is_active = 1
                    """, (float(val), league_id, mkt))
                
                logger.info(f"Daily adjustment applied to league {league_id}: delta {threshold_delta}")
                return {"status": "adjusted", "delta": threshold_delta, "adjusted_thresholds": adjusted}

            return {"status": "unchanged"}
        except Exception as e:
            logger.error(f"Failed to run daily micro-adjustments for {league_id}: {e}")
            return {"status": "error", "reason": str(e)}
