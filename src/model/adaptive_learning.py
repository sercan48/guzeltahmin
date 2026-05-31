"""CLV Feedback Loop & Adaptive Learning Engine.

Processes closing line values (CLV) to dynamically adjust feature weights,
market biases, outcome thresholds, and evaluate system drift.
"""

import json
import logging
from pathlib import Path
import numpy as np

from config.settings import MODELS_DIR
from config.constants import FEATURE_COLUMNS, LABEL_MAP

logger = logging.getLogger(__name__)

# Config paths
PROJECT_ROOT = Path(__file__).parent.parent.parent
WEIGHTS_PATH = PROJECT_ROOT / "data" / "dynamic_feature_weights.json"
BIAS_PATH = PROJECT_ROOT / "data" / "market_bias_scores.json"
THRESHOLD_STATE_PATH = PROJECT_ROOT / "data" / "league_threshold_state.json"

# Targets and names mapping
TARGET_FEATURES = {
    "home_team_strength": "home_team_strength",
    "away_team_strength": "away_team_strength",
    "home_form_last5": "home_form_last5",
    "away_form_last5": "away_form_last5",
    "travel_distance_km": "travel_distance_km",
    "is_artificial_pitch": "is_artificial_pitch",
    "extreme_humidity": "extreme_humidity"
}

class AdaptiveLearningEngine:
    def __init__(self, db=None):
        self.db = db
        self.weights_path = WEIGHTS_PATH
        self.bias_path = BIAS_PATH
        self.thresholds_state_path = THRESHOLD_STATE_PATH
        self._initialize_files()

    def _initialize_files(self):
        """Ensure config files exist with neutral defaults."""
        WEIGHTS_PATH.parent.mkdir(parents=True, exist_ok=True)
        
        # 1. Dynamic Feature Weights (default to 0.0, range: [-1, 1])
        if not WEIGHTS_PATH.exists():
            default_weights = {feat: 0.0 for feat in TARGET_FEATURES}
            self._save_json(WEIGHTS_PATH, default_weights)

        # 2. Market Bias Scores (default to 0.5, range: [0, 1])
        if not BIAS_PATH.exists():
            default_bias = {
                "1": 0.5, "X": 0.5, "2": 0.5,
                "1X": 0.5, "X2": 0.5, "12": 0.5,
                "DNB1": 0.5, "DNB2": 0.5
            }
            self._save_json(BIAS_PATH, default_bias)

        # 3. League Adaptive Thresholds (default to empty dict)
        if not THRESHOLD_STATE_PATH.exists():
            self._save_json(THRESHOLD_STATE_PATH, {})

    def _load_json(self, path: Path) -> dict:
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Failed to load json from {path}: {e}")
            return {}

    def _save_json(self, path: Path, data: dict):
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.error(f"Failed to save json to {path}: {e}")

    def process_clv_feedback(self, match_id: int, selection: str, model_probability: float,
                             open_odds: float, close_odds: float, clv_pct: float) -> dict:
        """Process a single CLV feedback event at kickoff and return the adaptation/drift state."""
        if not self.db:
            return {"status": "skipped", "reason": "No DB connection"}

        # 1. Log Feedback to DB
        # Retrieve league_code and result from match
        m_row = self.db.fetchone("SELECT league_code, ft_result FROM matches WHERE id = ?", (match_id,))
        if not m_row:
            return {"status": "skipped", "reason": f"Match {match_id} not found"}

        league_code = m_row["league_code"]
        actual_result = m_row["ft_result"]

        try:
            self.db.execute("""
                INSERT INTO clv_feedback_log (
                    match_id, league_id, market_type, selection, model_probability,
                    market_open_odds, market_close_odds, clv_value, result
                ) VALUES (?, ?, '1X2', ?, ?, ?, ?, ?, ?)
            """, (match_id, league_code, selection, model_probability, open_odds, close_odds, clv_pct, actual_result))
        except Exception as db_err:
            logger.error(f"Failed to insert clv feedback log: {db_err}")

        # 2. Update Feature Weights using SHAP
        weights_updated = self._update_feature_weights(match_id, selection, clv_pct)

        # 3. Update Market Bias Scores
        bias_updated = self._update_market_bias(selection, clv_pct)

        # 4. Adaptive Threshold Update for the League
        threshold_updated = self._update_league_thresholds(league_code)

        # 5. Drift Detection & Alarm Level Generation
        drift_state = self.detect_drift()

        return {
            "status": "success",
            "weights_updated": weights_updated,
            "bias_updated": bias_updated,
            "threshold_updated": threshold_updated,
            "drift_state": drift_state
        }

    def _update_feature_weights(self, match_id: int, selection: str, clv_pct: float) -> bool:
        """Update feature weights based on SHAP value directions and CLV outcome."""
        try:
            from src.model.shap_explainer import SHAPExplainer
            from src.model.predictor import build_match_features
            
            # Retrieve match details
            m_row = self.db.fetchone("SELECT home_team_id, away_team_id, league_code, season FROM matches WHERE id = ?", (match_id,))
            if not m_row:
                return False

            # Rebuild features
            features = build_match_features(
                self.db, m_row["home_team_id"], m_row["away_team_id"],
                m_row["league_code"], m_row["season"], match_id=match_id
            )

            # Initialize SHAP explainer
            shap_exp = SHAPExplainer()
            if not shap_exp.explainer or not shap_exp.xgb_model:
                return False

            n_expected = getattr(shap_exp.xgb_model, 'n_features_in_', len(FEATURE_COLUMNS))
            use_columns = FEATURE_COLUMNS[:n_expected]
            X = np.array([[float(features.get(col) or 0.0) for col in use_columns]])

            # Resolve predicted outcome index for SHAP
            # predicted selection maps to index: 1 -> 0 (Home), X -> 1 (Draw), 2 -> 2 (Away)
            pred_map = {"1": 0, "X": 1, "2": 2, "H": 0, "D": 1, "A": 2}
            pred_idx = pred_map.get(selection, 0)

            # Compute raw SHAP values
            shap_values = shap_exp.explainer.shap_values(X)
            if isinstance(shap_values, list):
                class_shap = shap_values[pred_idx][0]
            elif shap_values.ndim == 3:
                class_shap = shap_values[0, :, pred_idx]
            else:
                class_shap = shap_values[0]

            # Load current weights
            weights = self._load_json(WEIGHTS_PATH)
            learning_rate = 0.01

            for f_name, f_key in TARGET_FEATURES.items():
                if f_key in use_columns:
                    f_idx = use_columns.index(f_key)
                    shap_val = float(class_shap[f_idx])
                    
                    # Update rule: increment if shap & clv align (same sign), decrement if opposite
                    if abs(shap_val) >= 1e-4 and clv_pct != 0.0:
                        sign_product = np.sign(shap_val) * np.sign(clv_pct)
                        current_w = weights.get(f_name, 0.0)
                        
                        # Apply learning update
                        new_w = current_w + learning_rate * sign_product
                        weights[f_name] = round(max(-1.0, min(1.0, new_w)), 4)

            self._save_json(WEIGHTS_PATH, weights)
            return True
        except Exception as e:
            logger.error(f"Failed to update feature weights: {e}")
            return False

    def _update_market_bias(self, selection: str, clv_pct: float) -> bool:
        """Update market bias scores for positive/negative CLVs."""
        try:
            bias = self._load_json(BIAS_PATH)
            learning_rate = 0.01

            # Normalize outcome key
            sel_key = selection
            if selection in ("H", "MS 1"): sel_key = "1"
            elif selection in ("D", "MS X"): sel_key = "X"
            elif selection in ("A", "MS 2"): sel_key = "2"

            if sel_key in bias:
                current_bias = bias[sel_key]
                if clv_pct > 0.0:
                    new_bias = current_bias + learning_rate
                elif clv_pct < 0.0:
                    new_bias = current_bias - learning_rate
                else:
                    return False
                
                bias[sel_key] = round(max(0.0, min(1.0, new_bias)), 4)
                self._save_json(BIAS_PATH, bias)
                return True
        except Exception as e:
            logger.error(f"Failed to update market bias: {e}")
        return False

    def _update_league_thresholds(self, league_code: str) -> bool:
        """Query rolling CLV and adapt league selection thresholds dynamically."""
        try:
            # Query last 10 predictions rolling CLV for this league
            row = self.db.fetchone("""
                SELECT AVG(clv_value) as avg_clv
                FROM clv_feedback_log
                WHERE league_id = ?
                ORDER BY timestamp DESC LIMIT 10
            """, (league_code,))

            if not row or row["avg_clv"] is None:
                return False

            avg_clv = float(row["avg_clv"])
            state = self._load_json(THRESHOLD_STATE_PATH)

            # Base thresholds to initialize if missing
            base_thresholds = {
                "1": 0.62, "2": 0.60, "X": 0.32,
                "1X": 0.75, "X2": 0.75, "12": 0.75,
                "DNB1": 0.65, "DNB2": 0.65
            }

            if league_code not in state:
                state[league_code] = base_thresholds.copy()

            # Rule: increase threshold if CLV is negative, decrease if consistently highly positive (> 5.0%)
            threshold_delta = 0.0
            if avg_clv < 0.0:
                threshold_delta = 0.01
            elif avg_clv > 5.0:
                threshold_delta = -0.005

            if threshold_delta != 0.0:
                for outcome in state[league_code]:
                    curr_val = state[league_code][outcome]
                    new_val = curr_val + threshold_delta
                    state[league_code][outcome] = round(max(0.55, min(0.80, new_val)), 4)
                
                self._save_json(THRESHOLD_STATE_PATH, state)
                return True
        except Exception as e:
            logger.error(f"Failed to update league thresholds: {e}")
        return False

    def detect_drift(self) -> dict:
        """Evaluate rolling metrics to trigger drift alerts (Level 1, Level 2, Level 3)."""
        if not self.db:
            return {"level": 0, "status": "No database connection"}

        try:
            # 1. 7-day rolling average CLV
            clv_row = self.db.fetchone("""
                SELECT AVG(clv_value) as avg_clv
                FROM clv_feedback_log
                WHERE timestamp >= datetime('now', '-7 days')
            """)
            avg_7d_clv = float(clv_row["avg_clv"]) if clv_row and clv_row["avg_clv"] is not None else 0.0

            # 2. Edge variance comparisons (7-day vs 30-day)
            edges_7d = [float(r["value_edge"]) for r in self.db.fetchall("""
                SELECT value_edge FROM predictions
                WHERE value_edge IS NOT NULL AND created_at >= datetime('now', '-7 days')
            """)]
            edges_30d = [float(r["value_edge"]) for r in self.db.fetchall("""
                SELECT value_edge FROM predictions
                WHERE value_edge IS NOT NULL AND created_at >= datetime('now', '-30 days')
            """)]

            var_7d = np.var(edges_7d) if edges_7d else 0.0
            var_30d = np.var(edges_30d) if edges_30d else 0.0

            # Drift Assessment Rules
            level = 0
            message = "System state healthy"

            if avg_7d_clv < -5.0:
                level = 3
                message = f"CRITICAL: Severe model degradation detected. 7-day rolling CLV is %{avg_7d_clv:.2f}. Full ML retraining required."
            elif avg_7d_clv < 0.0:
                level = 1
                message = f"WARNING: Negative 7-day rolling CLV detected (%{avg_7d_clv:.2f})."
            elif var_30d > 0 and (var_7d / var_30d) > 1.5:
                level = 1
                message = f"WARNING: Model variance shift detected. 7-day edge variance ({var_7d:.4f}) is {var_7d/var_30d:.1f}x higher than baseline."

            # Log to DB as a bot activity log if Level 1 or 3
            if level > 0:
                logger.warning(f"[DRIFT DETECTED] Level {level}: {message}")
                try:
                    self.db.execute("""
                        INSERT INTO bot_activity_log (telegram_id, command, details)
                        VALUES (0, 'drift_detector', ?)
                    """, (f"Level {level} - {message}",))
                except Exception:
                    pass

            return {
                "level": level,
                "message": message,
                "rolling_clv_7d": round(avg_7d_clv, 2),
                "variance_ratio": round(var_7d / var_30d, 2) if var_30d > 0 else 1.0
            }
        except Exception as e:
            logger.error(f"Failed to check drift: {e}")
            return {"level": 0, "error": str(e)}
