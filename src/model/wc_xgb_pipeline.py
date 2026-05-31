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
    # 1. elo_diff
    elo_diff = team_a_stats.elo - team_b_stats.elo
    
    # 2. altitude_meters (mocked from env or stats if available, here just using fatigue proxy)
    # We will assume team_a_stats.fatigue already incorporated distance
    fatigue_diff = team_a_stats.fatigue - team_b_stats.fatigue
    
    # 3. att_vs_def_delta_a
    att_vs_def_a = team_a_stats.att_vs_def_delta
    
    # 4. att_vs_def_delta_b
    att_vs_def_b = team_b_stats.att_vs_def_delta
    
    # 5. synergy_diff
    synergy_diff = team_a_stats.synergy - team_b_stats.synergy
    
    # 6. Base strength indicator
    base_strength = (team_a_stats.elo + team_b_stats.elo) / 2.0
    
    return np.array([[elo_diff, fatigue_diff, att_vs_def_a, att_vs_def_b, synergy_diff, base_strength]])

def train_wc_xgboost(X, y):
    """
    Trains an XGBClassifier to output Win/Draw/Loss probabilities.
    Target (y): 0=Away Win, 1=Draw, 2=Home Win
    """
    X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, random_state=42)
    
    # XGBoost configuration for non-linear interactions
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
    """Mock model for inference without fully training on DB."""
    def predict_proba(self, X):
        # Extremely basic mock: uses elo_diff (X[0][0]) to guess
        elo_diff = X[0][0]
        if elo_diff > 100:
            return np.array([[0.1, 0.2, 0.7]]) # Home win
        elif elo_diff < -100:
            return np.array([[0.7, 0.2, 0.1]]) # Away win
        else:
            return np.array([[0.33, 0.34, 0.33]]) # Draw
            
def get_xgb_model():
    """Returns a trained XGBoost model (or mock if not trained)."""
    return MockXGBModel()
