"""Hyperparameter tuning with Optuna for XGBoost."""

import optuna
import xgboost as xgb
import numpy as np
from sklearn.model_selection import cross_val_score

from config.settings import RANDOM_SEED
from src.model.trainer import prepare_training_data, FEATURE_COLUMNS


def objective(trial, X, y):
    """Optuna objective function for XGBoost tuning."""
    params = {
        "objective": "multi:softprob",
        "num_class": 3,
        "max_depth": trial.suggest_int("max_depth", 3, 10),
        "learning_rate": trial.suggest_float("learning_rate", 0.01, 0.3, log=True),
        "n_estimators": trial.suggest_int("n_estimators", 100, 1000),
        "subsample": trial.suggest_float("subsample", 0.6, 1.0),
        "colsample_bytree": trial.suggest_float("colsample_bytree", 0.6, 1.0),
        "min_child_weight": trial.suggest_int("min_child_weight", 1, 10),
        "gamma": trial.suggest_float("gamma", 0.0, 1.0),
        "reg_alpha": trial.suggest_float("reg_alpha", 0.0, 2.0),
        "reg_lambda": trial.suggest_float("reg_lambda", 0.5, 3.0),
        "random_state": RANDOM_SEED,
        "eval_metric": "mlogloss",
    }

    model = xgb.XGBClassifier(**params)
    scores = cross_val_score(model, X, y, cv=5, scoring="accuracy", n_jobs=-1)
    return scores.mean()


def tune_hyperparameters(features_df, n_trials: int = 100) -> dict:
    """Run Optuna hyperparameter search.

    Args:
        features_df: DataFrame with features and ft_result
        n_trials: Number of optimization trials

    Returns:
        Best parameters dict
    """
    print("=" * 60)
    print("  Optuna Hyperparameter Tuning")
    print("=" * 60)

    available = [c for c in FEATURE_COLUMNS if c in features_df.columns]
    X = features_df[available].fillna(0).values
    y = features_df["ft_result"].map({"H": 0, "D": 1, "A": 2}).values
    valid = ~np.isnan(y)
    X, y = X[valid], y[valid].astype(int)

    study = optuna.create_study(direction="maximize", study_name="xgb_football")
    study.optimize(lambda trial: objective(trial, X, y), n_trials=n_trials, show_progress_bar=True)

    print(f"\n[BEST] Accuracy: {study.best_value:.4f}")
    print(f"[BEST] Params: {study.best_params}")

    # Convert to training-ready params
    best = study.best_params.copy()
    best["objective"] = "multi:softprob"
    best["num_class"] = 3
    best["random_state"] = RANDOM_SEED
    best["eval_metric"] = "mlogloss"
    best["early_stopping_rounds"] = 30

    return best
