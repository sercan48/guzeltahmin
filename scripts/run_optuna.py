"""Run Optuna Hyperparameter Tuning for XGBoost Model.

Optimizes XGBoost parameters on our expanded 56k+ match dataset including
FIFA attributes, derby flags, referee strictness, and xG efficiency.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pandas as pd
from src.model.hypertuner import tune_hyperparameters
from src.model.trainer import full_training_pipeline
from config.settings import PROCESSED_DIR


def main():
    print("=" * 60)
    print("  Güzel Tahmin — Optuna Hyperparameter Tuning")
    print("=" * 60)

    features_path = PROCESSED_DIR / "features.csv"
    if not features_path.exists():
        print("[ERROR] features.csv not found. Run build_features.py first!")
        return

    df = pd.read_csv(features_path)
    print(f"[DATA] Loaded {len(df)} matches from features.csv")

    # Phase 1: Find best hyperparameters (20 trials)
    best_params = tune_hyperparameters(df, n_trials=20)
    
    print("\n" + "=" * 60)
    print("  Re-training with BEST found parameters...")
    print("=" * 60)

    # Phase 2: Retrain with best params and save the model
    model, accuracy, report = full_training_pipeline(df, params=best_params)

    print(f"\n[DONE] Final model accuracy: {accuracy:.4f}")


if __name__ == "__main__":
    main()
