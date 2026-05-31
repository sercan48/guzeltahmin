"""Market Builder & Bet Selector Engine.

Calculates derived market probabilities (Double Chance, Draw No Bet)
and selects the optimal recommendation based on quantitative thresholds.
"""

import logging
import numpy as np

logger = logging.getLogger(__name__)

class MarketBuilder:
    def __init__(self):
        pass

    def build_markets(self, h_prob: float, d_prob: float, a_prob: float, base_confidence: float) -> dict:
        """Derives Double Chance and Draw No Bet probabilities and confidence scores.

        Args:
            h_prob: Home win probability (0.0 to 1.0)
            d_prob: Draw probability (0.0 to 1.0)
            a_prob: Away win probability (0.0 to 1.0)
            base_confidence: Base confidence score (0.0 to 10.0)

        Returns:
            dict: Mapping of market names to their details (prob, confidence, derived_from, etc.)
        """
        # Normalize inputs just in case
        total = h_prob + d_prob + a_prob
        if total > 0:
            h_prob /= total
            d_prob /= total
            a_prob /= total
        else:
            h_prob, d_prob, a_prob = 0.33, 0.33, 0.33

        # Standard 1X2 Markets
        markets = {
            "1": {
                "market": "1",
                "probability": round(h_prob, 3),
                "confidence": round(base_confidence, 2),
                "derived_from": ["P1"]
            },
            "X": {
                "market": "X",
                "probability": round(d_prob, 3),
                "confidence": round(base_confidence * 0.8, 2),  # Draw is harder to call
                "derived_from": ["PX"]
            },
            "2": {
                "market": "2",
                "probability": round(a_prob, 3),
                "confidence": round(base_confidence, 2),
                "derived_from": ["P2"]
            }
        }

        # Double Chance (1X, X2, 12)
        p_1x = h_prob + d_prob
        p_x2 = a_prob + d_prob
        p_12 = h_prob + a_prob

        # DC Confidence scales with the combined probability
        markets["1X"] = {
            "market": "1X",
            "probability": round(p_1x, 3),
            "confidence": round(base_confidence * 0.7 + (p_1x * 3.0), 2),
            "derived_from": ["P1", "PX"]
        }
        markets["X2"] = {
            "market": "X2",
            "probability": round(p_x2, 3),
            "confidence": round(base_confidence * 0.7 + (p_x2 * 3.0), 2),
            "derived_from": ["PX", "P2"]
        }
        markets["12"] = {
            "market": "12",
            "probability": round(p_12, 3),
            "confidence": round(base_confidence * 0.7 + (p_12 * 3.0), 2),
            "derived_from": ["P1", "P2"]
        }

        # Draw No Bet (DNB Home, DNB Away)
        non_draw_sum = h_prob + a_prob
        if non_draw_sum > 0:
            p_dnb1 = h_prob / non_draw_sum
            p_dnb2 = a_prob / non_draw_sum
        else:
            p_dnb1 = p_dnb2 = 0.5

        # DNB Confidence is slightly adjusted since draw risk is removed
        markets["DNB1"] = {
            "market": "DNB1",
            "probability": round(p_dnb1, 3),
            "confidence": round(base_confidence * 0.85, 2),
            "derived_from": ["P1", "P2"]
        }
        markets["DNB2"] = {
            "market": "DNB2",
            "probability": round(p_dnb2, 3),
            "confidence": round(base_confidence * 0.85, 2),
            "derived_from": ["P1", "P2"]
        }

        return markets


class BetSelector:
    def __init__(self, thresholds_path: str = None):
        import json
        from pathlib import Path

        if thresholds_path is None:
            thresholds_path = Path(__file__).parent.parent.parent / "data" / "optimized_thresholds.json"
        else:
            thresholds_path = Path(thresholds_path)

        self.thresholds = {}
        if thresholds_path.exists():
            try:
                with open(thresholds_path, "r", encoding="utf-8") as f:
                    self.thresholds = json.load(f)
                logger.info(f"Loaded optimized thresholds for leagues: {list(self.thresholds.keys())}")
            except Exception as e:
                logger.warning(f"Failed to load optimized thresholds from {thresholds_path}: {e}")

        # 1. Load Dynamic Feature Weights
        self.weights_path = Path(__file__).parent.parent.parent / "data" / "dynamic_feature_weights.json"
        self.feature_weights = {}
        if self.weights_path.exists():
            try:
                with open(self.weights_path, "r", encoding="utf-8") as f:
                    self.feature_weights = json.load(f)
            except Exception as e:
                logger.warning(f"Failed to load dynamic feature weights: {e}")

        # 2. Load Market Bias Scores
        self.bias_path = Path(__file__).parent.parent.parent / "data" / "market_bias_scores.json"
        self.market_bias_scores = {}
        if self.bias_path.exists():
            try:
                with open(self.bias_path, "r", encoding="utf-8") as f:
                    self.market_bias_scores = json.load(f)
            except Exception as e:
                logger.warning(f"Failed to load market bias scores: {e}")

        # 3. Load Adaptive Thresholds State
        self.thresholds_state_path = Path(__file__).parent.parent.parent / "data" / "league_threshold_state.json"
        self.thresholds_state = {}
        if self.thresholds_state_path.exists():
            try:
                with open(self.thresholds_state_path, "r", encoding="utf-8") as f:
                    self.thresholds_state = json.load(f)
            except Exception as e:
                logger.warning(f"Failed to load league thresholds state: {e}")

    def get_threshold(self, league_type: str, outcome: str, league_code: str = None, db = None) -> float:
        """Get dynamic threshold for a specific outcome based on league_type, adaptive state, or DB threshold_state."""
        # 1. Try DB threshold_state first
        if db and league_code:
            try:
                row = db.fetchone("""
                    SELECT threshold_value FROM threshold_state 
                    WHERE league_id = ? AND market_type = ? AND is_active = 1
                """, (league_code, outcome))
                if row:
                    return float(row["threshold_value"])
            except Exception as e:
                logger.warning(f"Failed to fetch threshold from DB for {league_code}: {e}")

        # 2. Try DB threshold_state for league_type fallback
        if db and league_type:
            try:
                row = db.fetchone("""
                    SELECT threshold_value FROM threshold_state 
                    WHERE league_id = ? AND market_type = ? AND is_active = 1
                """, (league_type, outcome))
                if row:
                    return float(row["threshold_value"])
            except Exception:
                pass

        base_thresholds = {
            "EUROPE_STABLE": {
                "1": 0.62, "2": 0.60, "X": 0.32,
                "1X": 0.75, "X2": 0.75, "12": 0.75,
                "DNB1": 0.65, "DNB2": 0.65
            },
            "SUMMER_VOLATILE": {
                "1": 0.60, "2": 0.58, "X": 0.33,
                "1X": 0.70, "X2": 0.70, "12": 0.70,
                "DNB1": 0.60, "DNB2": 0.60
            },
            "HIGH_ROTATION": {
                "1": 0.65, "2": 0.62, "X": 0.34,
                "1X": 0.78, "X2": 0.78, "12": 0.78,
                "DNB1": 0.68, "DNB2": 0.68
            }
        }

        l_type = league_type or "HIGH_ROTATION"
        defaults = base_thresholds.get(l_type, base_thresholds["HIGH_ROTATION"])

        # 3. Try local adaptive state files
        base_val = None
        if league_code and self.thresholds_state and league_code in self.thresholds_state:
            base_val = self.thresholds_state[league_code].get(outcome)
        elif self.thresholds and l_type in self.thresholds:
            base_val = self.thresholds[l_type].get(outcome)

        if base_val is None:
            base_val = defaults.get(outcome, 0.60)

        # Apply market multipliers for baseline fallback configurations
        multipliers = {
            "1": 1.05, "X": 1.05, "2": 1.05,
            "1X": 0.98, "X2": 0.98, "12": 0.98,
            "DNB1": 0.92, "DNB2": 0.92
        }
        factor = multipliers.get(outcome, 1.0)
        final_val = base_val * factor

        # Clamp bounds
        min_b, max_b = (0.28, 0.45) if outcome == "X" else (0.55, 0.80)
        return round(max(min_b, min(final_val, max_b)), 4)

    def get_preference_bonus(self, league_type: str, market_type: str) -> float:
        """Get preference bonus for a given market type and league type."""
        if league_type == "EUROPE_STABLE":
            if market_type == "1X2":
                return 0.05
        elif league_type == "SUMMER_VOLATILE":
            if market_type in ["DC", "DNB"]:
                return 0.05
        return 0.0

    def calculate_decision_score(self, prob: float, confidence: float, sample_size: int, coverage: float,
                                 edge: float = 0.0, avg_clv: float = 0.0, is_derby: bool = False,
                                 power_loss_pct: float = 0.0, model_agreement: float = 1.0,
                                 clv_feedback_score: float = 0.0, market_bias_adjustment: float = 0.0) -> float:
        """Calculate 4-factor Decision Score (Probability, Value, CLV History, Risk Penalties) with adaptive extensions."""
        # 1. ProbabilityScore (standardized base)
        conf_factor = 0.85 + 0.30 * (confidence / 10.0)
        sample_size_factor = 1.0 - 0.15 * np.exp(-sample_size / 50.0)
        coverage_factor = 0.9 + 0.2 * min(max(coverage, 0.0), 1.0)
        prob_score = float(prob * conf_factor * sample_size_factor * coverage_factor)

        # 2. ValueScore (scaled weight of 1.2 on market edge)
        value_score = 1.2 * edge

        # 3. CLVHistoryScore (scaled weight of 0.5 on average CLV percentage)
        clv_history_score = 0.5 * (avg_clv / 100.0)

        # 4. RiskPenalty
        risk_penalty = 0.05 * float(is_derby) + 0.04 * power_loss_pct + 0.03 * (1.0 - model_agreement)

        # Final decision score blending core factors and dynamic updates
        decision_score = prob_score + value_score + clv_history_score + clv_feedback_score + market_bias_adjustment - risk_penalty
        return float(decision_score)

    def select_best_bet(self, markets: dict, league_code: str = None, db = None, odds: dict = None,
                        match_id: int = None, features: dict = None) -> dict:
        """Selects the optimal bet recommendation by ranking all markets (1X2, DC, DNB) using the adaptive scores.

        Enforces rules: edge >= minimum_edge (2%) and calibrated_probability >= threshold.
        """
        from src.model.league_classifier import LeagueClassifier
        from src.model.value_clv_engine import clean_implied_probabilities, get_historical_clv
        from config.settings import MODELS_DIR
        import json

        # 1. Resolve league type
        classifier = LeagueClassifier(db)
        league_type = classifier.get_league_type(league_code)

        # 2. Fetch odds if not provided
        if not odds and db and match_id:
            try:
                snap_rows = db.fetchall("""
                    SELECT selection, odds FROM odds_snapshots
                    WHERE match_id = ? AND market_type = '1X2'
                    ORDER BY id DESC
                """, (match_id,))
                if snap_rows:
                    odds = {}
                    for r in snap_rows:
                        if r["selection"] == "1": odds["home_odds"] = r["odds"]
                        elif r["selection"] == "X": odds["draw_odds"] = r["odds"]
                        elif r["selection"] == "2": odds["away_odds"] = r["odds"]
                
                if not odds or "home_odds" not in odds:
                    o_row = db.fetchone("SELECT * FROM odds WHERE match_id = ? ORDER BY id DESC LIMIT 1", (match_id,))
                    if o_row:
                        odds = {
                            "home_odds": o_row.get("home_odds"),
                            "draw_odds": o_row.get("draw_odds"),
                            "away_odds": o_row.get("away_odds"),
                            "over25_odds": o_row.get("over25_odds"),
                            "under25_odds": o_row.get("under25_odds"),
                        }
            except Exception:
                pass

        # 3. Clean market probabilities
        clean_h, clean_d, clean_a = 0.333, 0.333, 0.334
        if odds:
            o_h = odds.get("home_odds") or odds.get("h")
            o_d = odds.get("draw_odds") or odds.get("d")
            o_a = odds.get("away_odds") or odds.get("a")
            if o_h and o_d and o_a:
                clean_h, clean_d, clean_a = clean_implied_probabilities(o_h, o_d, o_a)

        # 4. Fetch risk parameters
        sample_size = 100
        if db and league_code:
            try:
                row = db.fetchone(
                    "SELECT COUNT(*) as count FROM matches WHERE league_code = ? AND ft_result IS NOT NULL",
                    (league_code,)
                )
                if row and row["count"] > 0:
                    sample_size = row["count"]
            except Exception:
                pass

        coverage = 0.20
        if league_code:
            meta_path = MODELS_DIR / "ensemble" / f"calibrator_{league_code}_meta.json"
            if meta_path.exists():
                try:
                    with open(meta_path, "r", encoding="utf-8") as f:
                        meta_data = json.load(f)
                        coverage = meta_data.get("coverage", 0.20)
                except Exception:
                    pass

        is_derby = False
        power_loss_pct = 0.0
        model_agreement = 1.0
        avg_clv = 0.0

        if db:
            if match_id:
                try:
                    m_row = db.fetchone("SELECT home_team_id, away_team_id, importance, league_code, season FROM matches WHERE id = ?", (match_id,))
                    if m_row:
                        is_derby = (m_row.get("importance") == "derby")
                        from src.agents.data_agent import get_team_status_from_db
                        h_stat = get_team_status_from_db(db, m_row["home_team_id"])
                        a_stat = get_team_status_from_db(db, m_row["away_team_id"])
                        h_loss = h_stat.get("power_loss_pct", 0.0) if h_stat else 0.0
                        a_loss = a_stat.get("power_loss_pct", 0.0) if a_stat else 0.0
                        power_loss_pct = (h_loss + a_loss) / 100.0
                        
                        if not features:
                            from src.model.predictor import build_match_features
                            features = build_match_features(db, m_row["home_team_id"], m_row["away_team_id"], m_row["league_code"], m_row["season"], match_id=match_id)
                except Exception:
                    pass
            if league_code:
                avg_clv = get_historical_clv(db, league_code)

        # 5. Load base SHAP values for the match if features are available
        shap_h = shap_d = shap_a = None
        use_columns = []
        if features:
            try:
                from src.model.shap_explainer import SHAPExplainer
                from config.constants import FEATURE_COLUMNS
                shap_exp = SHAPExplainer()
                if shap_exp.explainer and shap_exp.xgb_model:
                    n_expected = getattr(shap_exp.xgb_model, 'n_features_in_', len(FEATURE_COLUMNS))
                    use_columns = FEATURE_COLUMNS[:n_expected]
                    X = np.array([[float(features.get(col) or 0.0) for col in use_columns]])
                    shap_values = shap_exp.explainer.shap_values(X)
                    
                    if isinstance(shap_values, list):
                        shap_h = shap_values[0][0]
                        shap_d = shap_values[1][0]
                        shap_a = shap_values[2][0]
                    elif shap_values.ndim == 3:
                        shap_h = shap_values[0, :, 0]
                        shap_d = shap_values[0, :, 1]
                        shap_a = shap_values[0, :, 2]
                    else:
                        shap_h = shap_values[0]
                        shap_d = shap_values[0]
                        shap_a = shap_values[0]
            except Exception as shap_err:
                logger.debug(f"SHAP explanation failed inside BetSelector: {shap_err}")

        # 6. Define candidate outcomes
        market_types = {
            "1": "1X2", "X": "1X2", "2": "1X2",
            "1X": "DC", "X2": "DC", "12": "DC",
            "DNB1": "DNB", "DNB2": "DNB"
        }

        scored_candidates = []
        for outcome, m_info in markets.items():
            prob = m_info["probability"]
            confidence = m_info["confidence"]
            mkt_type = market_types[outcome]

            # Calculate edge
            if outcome == "1":
                edge = prob - clean_h
            elif outcome == "X":
                edge = prob - clean_d
            elif outcome == "2":
                edge = prob - clean_a
            elif outcome == "1X":
                edge = prob - (clean_h + clean_d)
            elif outcome == "X2":
                edge = prob - (clean_a + clean_d)
            elif outcome == "12":
                edge = prob - (clean_h + clean_a)
            elif outcome == "DNB1":
                edge = prob - (clean_h / max(clean_h + clean_a, 0.01))
            elif outcome == "DNB2":
                edge = prob - (clean_a / max(clean_h + clean_a, 0.01))
            else:
                edge = 0.0

            # Derive CLV Feedback Score based on SHAP values
            clv_feedback_score = 0.0
            if shap_h is not None and self.feature_weights:
                if outcome == "1":
                    outcome_shap = shap_h
                elif outcome == "X":
                    outcome_shap = shap_d
                elif outcome == "2":
                    outcome_shap = shap_a
                elif outcome == "1X":
                    outcome_shap = shap_h + shap_d
                elif outcome == "X2":
                    outcome_shap = shap_a + shap_d
                elif outcome == "12":
                    outcome_shap = shap_h + shap_a
                elif outcome == "DNB1":
                    outcome_shap = shap_h - shap_a
                elif outcome == "DNB2":
                    outcome_shap = shap_a - shap_h
                else:
                    outcome_shap = np.zeros_like(shap_h)

                from src.model.adaptive_learning import TARGET_FEATURES
                for f_name, f_key in TARGET_FEATURES.items():
                    if f_key in use_columns:
                        f_idx = use_columns.index(f_key)
                        f_shap = float(outcome_shap[f_idx])
                        f_weight = self.feature_weights.get(f_name, 0.0)
                        clv_feedback_score += f_weight * f_shap

            # Calculate Market Bias Adjustment
            bias_score = self.market_bias_scores.get(outcome, 0.5)
            market_bias_adjustment = 0.1 * (bias_score - 0.5)

            # Calculate Decision Score
            ds = self.calculate_decision_score(
                prob, confidence, sample_size, coverage,
                edge=edge, avg_clv=avg_clv, is_derby=is_derby,
                power_loss_pct=power_loss_pct, model_agreement=model_agreement,
                clv_feedback_score=clv_feedback_score,
                market_bias_adjustment=market_bias_adjustment
            )

            # Apply Preference Bonus
            bonus = self.get_preference_bonus(league_type, mkt_type)
            score = ds + bonus

            # Check threshold
            threshold = self.get_threshold(league_type, outcome, league_code=league_code, db=db)

            scored_candidates.append({
                "outcome": outcome,
                "score": score,
                "decision_score": ds,
                "threshold": threshold,
                "probability": prob,
                "confidence": confidence,
                "edge": edge,
                "derived_from": m_info["derived_from"]
            })

        # Sort candidates
        scored_candidates.sort(key=lambda x: x["score"], reverse=True)

        # Enforce dynamic selection rules (edge >= 2%)
        minimum_edge = 0.02
        for cand in scored_candidates:
            if cand["edge"] >= minimum_edge and cand["probability"] >= cand["threshold"]:
                return {
                    "decision": "PLAY",
                    "market": cand["outcome"],
                    "probability": cand["probability"],
                    "confidence": cand["confidence"],
                    "derived_from": cand["derived_from"],
                    "score": round(cand["score"], 4),
                    "decision_score": round(cand["decision_score"], 4),
                    "threshold": round(cand["threshold"], 4),
                    "edge": round(cand["edge"], 4),
                    "league_type": league_type
                }

        return {
            "decision": "SKIP",
            "market": "SKIP",
            "probability": 0.0,
            "confidence": 0.0,
            "derived_from": [],
            "score": 0.0,
            "decision_score": 0.0,
            "threshold": 0.0,
            "edge": 0.0,
            "league_type": league_type
        }
