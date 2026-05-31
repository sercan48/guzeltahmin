"""SHAP Explainability Layer.

Computes feature contributions (SHAP values) for stacking ensemble base models
to generate human-readable prediction card details.
"""

import logging
import pickle
import numpy as np
from pathlib import Path
import shap

from config.settings import MODELS_DIR
from config.constants import FEATURE_COLUMNS

logger = logging.getLogger(__name__)

# Feature translation dictionary for Turkish Telegram cards
FEATURE_TRANSLATIONS = {
    "home_team_strength": "Ev Sahibi ELO gücü",
    "away_team_strength": "Deplasman ELO gücü",
    "home_form_last5": "Ev Sahibi son form durumu",
    "away_form_last5": "Deplasman son form durumu",
    "home_attack_rating": "Ev Sahibi hücum reytingi",
    "home_defense_rating": "Ev Sahibi savunma reytingi",
    "away_attack_rating": "Deplasman hücum reytingi",
    "away_defense_rating": "Deplasman savunma reytingi",
    "home_goals_scored_avg": "Ev Sahibi gol ortalaması",
    "home_goals_conceded_avg": "Ev Sahibi yediği gol ortalaması",
    "away_goals_scored_avg": "Deplasman gol ortalaması",
    "away_goals_conceded_avg": "Deplasman yediği gol ortalaması",
    "h2h_home_winrate": "Kafakafaya (H2H) geçmişi",
    "h2h_goals_avg": "H2H gol ortalaması",
    "home_advantage_factor": "Ev sahibi avantajı",
    "referee_strictness": "Hakem kart sıklığı",
    "home_squad_value": "Ev Sahibi kadro değeri",
    "away_squad_value": "Deplasman kadro değeri",
    "form_momentum_diff": "Form momentum farkı",
    "league_position_diff": "Lig sıra farkı",
    "is_derby": "Derbi maçı atmosferi",
    "red_card_risk": "Kırmızı kart riski",
    "home_xg_efficiency": "Ev Sahibi xG bitiricilik verimliliği",
    "away_xg_efficiency": "Deplasman xG bitiricilik verimliliği",
    "home_xg_avg": "Ev Sahibi xG ortalaması",
    "away_xg_avg": "Deplasman xG ortalaması",
    "home_congestion_score": "Ev Sahibi fikstür yoğunluğu yorgunluğu",
    "away_congestion_score": "Deplasman fikstür yoğunluğu yorgunluğu",
    "congestion_advantage": "Dinlenme/Fikstür avantajı",
    "clean_sheet_rate_diff": "Gole kapama oran farkı",
    "travel_distance_km": "Seyahat mesafesi (Yol yorgunluğu)",
    "is_artificial_pitch": "Yapay çim saha etkisi",
    "cup_rotation_fatigue": "Kupa rotasyonu yorgunluğu",
    "dp_presence": "DP (Designated Player) etkisi",
    "extreme_humidity": "Aşırı nem/Hava koşulları",
    "implied_home_prob": "Piyasa ev sahibi beklentisi",
    "implied_away_prob": "Piyasa deplasman beklentisi",
    "implied_draw_prob": "Piyasa beraberlik beklentisi",
}

class SHAPExplainer:
    def __init__(self, model_dir: Path = None):
        self.model_dir = model_dir or (MODELS_DIR / "ensemble")
        self.explainer = None
        self.xgb_model = None
        self._load_explainer()

    def _load_explainer(self):
        xgb_path = self.model_dir / "xgb.pkl"
        if xgb_path.exists():
            try:
                with open(xgb_path, "rb") as f:
                    self.xgb_model = pickle.load(f)
                # TreeExplainer is highly optimized for tree-based models like XGBoost
                self.explainer = shap.TreeExplainer(self.xgb_model)
                logger.info("[SHAP] Successfully initialized SHAP TreeExplainer for XGBoost model.")
            except Exception as e:
                logger.warning(f"[SHAP] Failed to initialize SHAP explainer: {e}")

    def explain_match(self, X: np.ndarray, predicted_class_idx: int) -> list[dict]:
        """Compute SHAP feature importance for a single match prediction.
        
        Args:
            X: shape (1, n_features) feature vector
            predicted_class_idx: 0 for Home, 1 for Draw, 2 for Away
            
        Returns:
            list[dict]: List of explanation factors containing direction, text, and impact level.
        """
        if self.explainer is None:
            return []

        try:
            # Compute SHAP values
            shap_values = self.explainer.shap_values(X)
            
            # For multi-class, shap_values can be a list of arrays (one per class) or a 3D array
            # XGBoost multi-class yields list or shape (n_samples, n_features, n_classes)
            if isinstance(shap_values, list):
                class_shap = shap_values[predicted_class_idx][0]
            elif shap_values.ndim == 3:
                class_shap = shap_values[0, :, predicted_class_idx]
            else:
                class_shap = shap_values[0]

            # Get feature names
            n_features = X.shape[1]
            feature_names = FEATURE_COLUMNS[:n_features]

            # Pair features with their SHAP values
            paired = []
            for i, name in enumerate(feature_names):
                val = float(class_shap[i])
                paired.append((name, val, abs(val)))

            # Sort by absolute SHAP values descending
            paired.sort(key=lambda x: x[2], reverse=True)

            factors = []
            # Take top 4 features that contribute significantly
            for name, val, abs_val in paired[:4]:
                if abs_val < 0.01:
                    continue
                    
                direction = "+" if val > 0 else "-"
                
                # Determine impact label
                if abs_val > 0.15:
                    impact = "high"
                elif abs_val > 0.05:
                    impact = "medium"
                else:
                    impact = "low"

                # Translate feature name to Turkish
                display_name = FEATURE_TRANSLATIONS.get(name, name)
                
                # Custom formatted explanations based on direction
                if direction == "+":
                    text = f"{display_name} tahmini destekliyor"
                else:
                    text = f"{display_name} tahmini olumsuz etkiliyor"

                factors.append({
                    "direction": direction,
                    "text": text,
                    "impact": impact,
                    "feature": name,
                    "shap_value": val
                })

            return factors

        except Exception as e:
            logger.warning(f"[SHAP] Error explaining match: {e}")
            return []
