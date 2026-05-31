"""Audit Feature Importance of current Ensemble Level-0 Models.

Inspects feature usage and prints a detailed report of the current features.
"""

import sys
import pickle
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from config.settings import MODELS_DIR
from config.constants import FEATURE_COLUMNS

def audit():
    print("=" * 60)
    print("  FEATURE IMPORTANCE AUDIT")
    print("=" * 60)

    ensemble_path = MODELS_DIR / "ensemble"
    
    if not (ensemble_path / "meta.pkl").exists():
        print(f"Meta model not found at {ensemble_path / 'meta.pkl'}")
        return

    # Load Level-0 models
    with open(ensemble_path / "xgb.pkl", "rb") as f:
        xgb_model = pickle.load(f)
    with open(ensemble_path / "lgb.pkl", "rb") as f:
        lgb_model = pickle.load(f)
    with open(ensemble_path / "meta.pkl", "rb") as f:
        meta_model = pickle.load(f)

    # Check number of features expected by base models
    n_xgb_features = xgb_model.n_features_in_ if hasattr(xgb_model, 'n_features_in_') else len(FEATURE_COLUMNS)
    n_lgb_features = lgb_model.n_features_in_ if hasattr(lgb_model, 'n_features_in_') else len(FEATURE_COLUMNS)

    print(f"XGBoost expects {n_xgb_features} features.")
    print(f"LightGBM expects {n_lgb_features} features.")
    print(f"Total features in FEATURE_COLUMNS: {len(FEATURE_COLUMNS)}")

    # Summer league features to check
    summer_cols = ["travel_distance_km", "is_artificial_pitch", "cup_rotation_fatigue", "dp_presence", "extreme_humidity"]
    
    print("\nChecking for summer features in model feature set:")
    for col in summer_cols:
        in_columns = col in FEATURE_COLUMNS
        print(f"  - '{col}': {'PRESENT' if in_columns else 'ABSENT'}")

    # XGBoost feature importance (if available)
    if hasattr(xgb_model, 'feature_importances_'):
        print("\n--- XGBoost Top 10 Features (Gini Importance) ---")
        importances = xgb_model.feature_importances_
        indices = importances.argsort()[::-1]
        for i in range(min(10, len(indices))):
            idx = indices[i]
            col_name = FEATURE_COLUMNS[idx] if idx < len(FEATURE_COLUMNS) else f"feature_{idx}"
            print(f"  {i+1}. {col_name}: {importances[idx]:.4f}")

    # LightGBM feature importance (if available)
    if hasattr(lgb_model, 'feature_importances_'):
        print("\n--- LightGBM Top 10 Features (Split Count) ---")
        importances = lgb_model.feature_importances_
        indices = importances.argsort()[::-1]
        for i in range(min(10, len(indices))):
            idx = indices[i]
            col_name = FEATURE_COLUMNS[idx] if idx < len(FEATURE_COLUMNS) else f"feature_{idx}"
            print(f"  {i+1}. {col_name}: {importances[idx]}")

    # Meta-learner coefficients
    if hasattr(meta_model, 'coef_'):
        print("\n--- Meta Stacking Layer Coefficients ---")
        # Class names
        classes = ["Home (0)", "Draw (1)", "Away (2)"]
        # Meta-learner has inputs: XGB [H, D, A], LGB [H, D, A], Poisson [H, D, A]
        meta_features = [
            "XGB_Home", "XGB_Draw", "XGB_Away",
            "LGB_Home", "LGB_Draw", "LGB_Away",
            "Poisson_Home", "Poisson_Draw", "Poisson_Away"
        ]
        for cls_idx, cls_name in enumerate(classes):
            print(f"  For target: {cls_name}")
            coefs = meta_model.coef_[cls_idx]
            for f_idx, f_name in enumerate(meta_features):
                if f_idx < len(coefs):
                    print(f"    * {f_name}: {coefs[f_idx]:.4f}")

if __name__ == "__main__":
    audit()
