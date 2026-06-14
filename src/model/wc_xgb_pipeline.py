import logging
import numpy as np
try:
    import xgboost as xgb
    from sklearn.model_selection import train_test_split
except ImportError:
    pass # Will be handled by environment

logger = logging.getLogger(__name__)

def prepare_xgb_features(team_a_stats, team_b_stats):
    """
    Extracts non-linear features tailored for XGBoost.
    Returns a numpy array of shape (1, 6).
    """
    elo_diff     = team_a_stats.elo - team_b_stats.elo
    fatigue_diff = team_a_stats.fatigue - team_b_stats.fatigue
    att_vs_def_a = team_a_stats.att_vs_def_delta
    att_vs_def_b = team_b_stats.att_vs_def_delta
    synergy_diff = team_a_stats.synergy - team_b_stats.synergy
    base_strength = (team_a_stats.elo + team_b_stats.elo) / 2.0

    return np.array([[elo_diff, fatigue_diff, att_vs_def_a, att_vs_def_b, synergy_diff, base_strength]])

def train_wc_xgboost(X, y):
    """
    Trains an XGBClassifier to output Win/Draw/Loss probabilities.
    Target (y): 0=Away Win, 1=Draw, 2=Home Win
    """
    X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, random_state=42)

    model = xgb.XGBClassifier(
        objective='multi:softprob',
        num_class=3,
        max_depth=4,
        learning_rate=0.05,
        n_estimators=200,
        random_state=42
    )
    model.fit(X_train, y_train)
    return model


class MockXGBModel:
    """Retained for backward compatibility. Superseded by WCIntelligenceXGBAdapter."""
    def predict_proba(self, X):
        elo_diff = X[0][0]
        if elo_diff > 100:
            return np.array([[0.1, 0.2, 0.7]])
        elif elo_diff < -100:
            return np.array([[0.7, 0.2, 0.1]])
        else:
            return np.array([[0.33, 0.34, 0.33]])


class WCIntelligenceXGBAdapter:
    """
    Drop-in replacement for MockXGBModel backed by the intelligence engine's
    GBM-style deterministic model.

    Feature vector expected (from prepare_xgb_features):
      X[0] = [elo_diff, fatigue_diff, att_vs_def_a, att_vs_def_b, synergy_diff, base_strength]

    Output: np.ndarray[[P(away), P(draw), P(home)]] — same convention as MockXGBModel.
    """

    def predict_proba(self, X: np.ndarray) -> np.ndarray:
        from src.model.wc_intelligence_engine import _gbm_predict_from_features

        elo_diff    = float(X[0][0])
        fatigue_diff = float(X[0][1])
        att_vs_def_a = float(X[0][2])
        att_vs_def_b = float(X[0][3])
        synergy_diff = float(X[0][4])

        att_def_delta = att_vs_def_a - att_vs_def_b

        p_home, p_draw, p_away = _gbm_predict_from_features(
            elo_diff, att_def_delta, synergy_diff, fatigue_diff
        )
        return np.array([[p_away, p_draw, p_home]])


def get_xgb_model():
    """Returns the intelligence-engine-backed model (interface-compatible with MockXGBModel)."""
    return WCIntelligenceXGBAdapter()
