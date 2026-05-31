"""Optuna Ensemble Weight Optimizer — minimize Brier score.

Optimizes blending weights for XGBoost, LightGBM, and Poisson outputs.
Constraint: w_xgb + w_lgb + w_poi = 1.0

Also fits and saves a probability calibrator (Isotonic Regression per class)
after finding the best weights.

Usage:
    python scripts/optimize_ensemble.py [--n-trials 100] [--n-folds 5]
"""

import sys
import pickle
import argparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import numpy as np
import pandas as pd
import optuna
from sklearn.model_selection import StratifiedKFold
from sklearn.metrics import brier_score_loss
from sklearn.isotonic import IsotonicRegression

from config.settings import MODELS_DIR, PROCESSED_DIR, RANDOM_SEED
from config.constants import FEATURE_COLUMNS, LABEL_MAP

optuna.logging.set_verbosity(optuna.logging.WARNING)


def _load_features() -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Load features.csv and return X, y, home_goals, away_goals."""
    path = PROCESSED_DIR / "features.csv"
    if not path.exists():
        raise FileNotFoundError(f"{path} not found. Run build_features.py first.")

    df = pd.read_csv(path)
    print(f"[DATA] Loaded {len(df)} matches")

    # Filter out future matches or matches without results
    df = df[df["ft_result"].isin(["H", "D", "A"])].copy()
    print(f"[DATA] Kept {len(df)} matches with valid results (no NaNs)")

    # Target
    y = df["ft_result"].map(LABEL_MAP).values.astype(int)

    # Features
    available_cols = [c for c in FEATURE_COLUMNS if c in df.columns]
    X = df[available_cols].fillna(0).values

    home_goals = df["ft_home_goals"].values if "ft_home_goals" in df.columns else None
    away_goals = df["ft_away_goals"].values if "ft_away_goals" in df.columns else None

    print(f"[DATA] Features: {len(available_cols)}, Classes: {np.unique(y)}")
    return X, y, home_goals, away_goals


def _get_oof_predictions(X, y, home_goals, away_goals, n_folds=5):
    """Generate out-of-fold predictions from all 3 base models."""
    from src.model.trainer import train_model as train_xgb
    from src.model.lightgbm_model import LightGBMTrainer
    from src.model.poisson_model import PoissonGoalPredictor

    kf = StratifiedKFold(n_splits=n_folds, shuffle=True, random_state=RANDOM_SEED)
    n = len(X)

    oof_xgb = np.zeros((n, 3))
    oof_lgb = np.zeros((n, 3))
    oof_poi = np.zeros((n, 3))

    print(f"\n[OOF] Generating {n_folds}-fold out-of-fold predictions...")

    for fold, (tr_idx, val_idx) in enumerate(kf.split(X, y)):
        X_tr, X_val = X[tr_idx], X[val_idx]
        y_tr, y_val = y[tr_idx], y[val_idx]

        # XGBoost
        xgb_m, _, _ = train_xgb(X_tr, y_tr, X_val, y_val)
        oof_xgb[val_idx] = xgb_m.predict_proba(X_val)

        # LightGBM
        lgb_trainer = LightGBMTrainer()
        lgb_m, _, _ = lgb_trainer.train(X_tr, y_tr, X_val, y_val)
        oof_lgb[val_idx] = lgb_m.predict_proba(X_val)

        # Poisson
        if home_goals is not None and away_goals is not None:
            poi = PoissonGoalPredictor()
            poi.train(X_tr, home_goals[tr_idx], away_goals[tr_idx])
            oof_poi[val_idx] = poi.predict_proba_batch(X_val)
        else:
            oof_poi[val_idx] = oof_xgb[val_idx]

        print(f"  Fold {fold+1}/{n_folds} done")

    return oof_xgb, oof_lgb, oof_poi


def _multiclass_brier(y_true: np.ndarray, probs: np.ndarray) -> float:
    """Compute multi-class Brier score (lower = better).

    Brier = (1/N) * Σ Σ (p_ij - y_ij)²
    """
    n_classes = probs.shape[1]
    n_samples = len(y_true)
    one_hot = np.zeros((n_samples, n_classes))
    for i, label in enumerate(y_true):
        one_hot[i, label] = 1.0
    return float(np.mean(np.sum((probs - one_hot) ** 2, axis=1)))


def _blend(xgb, lgb, poi, w_xgb, w_lgb, w_poi) -> np.ndarray:
    """Blend 3 model outputs with given weights."""
    blended = w_xgb * xgb + w_lgb * lgb + w_poi * poi
    # Normalize rows to sum=1
    row_sums = blended.sum(axis=1, keepdims=True)
    row_sums = np.where(row_sums == 0, 1, row_sums)
    return blended / row_sums


def run_optimization(n_trials: int = 100, n_folds: int = 5):
    """Main optimization pipeline."""
    X, y, home_goals, away_goals = _load_features()
    oof_xgb, oof_lgb, oof_poi = _get_oof_predictions(X, y, home_goals, away_goals, n_folds)

    # Individual model Brier scores
    brier_xgb = _multiclass_brier(y, oof_xgb)
    brier_lgb = _multiclass_brier(y, oof_lgb)
    brier_poi = _multiclass_brier(y, oof_poi)
    print(f"\n[BASELINE] Brier Scores:")
    print(f"  XGBoost:  {brier_xgb:.6f}")
    print(f"  LightGBM: {brier_lgb:.6f}")
    print(f"  Poisson:  {brier_poi:.6f}")

    # Optuna objective
    def objective(trial):
        w_xgb = trial.suggest_float("w_xgb", 0.05, 0.80)
        w_lgb = trial.suggest_float("w_lgb", 0.05, 0.80)
        w_poi = 1.0 - w_xgb - w_lgb

        # Constraint: all weights must be positive
        if w_poi < 0.02:
            return float("inf")

        blended = _blend(oof_xgb, oof_lgb, oof_poi, w_xgb, w_lgb, w_poi)
        return _multiclass_brier(y, blended)

    # Run Optuna
    print(f"\n[OPTUNA] Running {n_trials} trials...")
    study = optuna.create_study(
        direction="minimize",
        sampler=optuna.samplers.TPESampler(seed=RANDOM_SEED),
        study_name="ensemble_weights",
    )
    study.optimize(objective, n_trials=n_trials, show_progress_bar=True)

    best = study.best_params
    w_xgb = best["w_xgb"]
    w_lgb = best["w_lgb"]
    w_poi = 1.0 - w_xgb - w_lgb

    blended_probs = _blend(oof_xgb, oof_lgb, oof_poi, w_xgb, w_lgb, w_poi)
    best_brier = _multiclass_brier(y, blended_probs)

    print(f"\n{'='*60}")
    print(f"  BEST WEIGHTS (Brier: {best_brier:.6f})")
    print(f"{'='*60}")
    print(f"  XGBoost:  {w_xgb:.4f}")
    print(f"  LightGBM: {w_lgb:.4f}")
    print(f"  Poisson:  {w_poi:.4f}")
    print(f"{'='*60}")

    # Save weights
    weights_path = MODELS_DIR / "ensemble" / "blend_weights.pkl"
    weights_path.parent.mkdir(parents=True, exist_ok=True)
    with open(weights_path, "wb") as f:
        pickle.dump({"w_xgb": w_xgb, "w_lgb": w_lgb, "w_poi": w_poi,
                      "brier_score": best_brier}, f)
    print(f"[SAVED] Weights -> {weights_path}")

    # ── Fit & Save Isotonic Calibrator ─────────────────────────────
    print("\n[CALIBRATION] Fitting Isotonic Regression per class...")
    calibrator = {}
    for cls_idx, cls_name in enumerate(["H", "D", "A"]):
        ir = IsotonicRegression(out_of_bounds="clip")
        binary_target = (y == cls_idx).astype(float)
        ir.fit(blended_probs[:, cls_idx], binary_target)
        calibrator[cls_idx] = ir

        # Quick reliability check
        cal_probs = ir.transform(blended_probs[:, cls_idx])
        cal_brier = brier_score_loss(binary_target, cal_probs)
        raw_brier = brier_score_loss(binary_target, blended_probs[:, cls_idx])
        print(f"  {cls_name}: Brier {raw_brier:.6f} -> {cal_brier:.6f} "
              f"({'improved' if cal_brier < raw_brier else 'same'})")

    cal_path = MODELS_DIR / "ensemble" / "calibrator.pkl"
    with open(cal_path, "wb") as f:
        pickle.dump(calibrator, f)
    print(f"[SAVED] Calibrator -> {cal_path}")

    # Summary
    print(f"\n{'='*60}")
    print(f"  OPTIMIZATION COMPLETE")
    print(f"  Baseline best single model Brier: {min(brier_xgb, brier_lgb, brier_poi):.6f}")
    print(f"  Optimized blend Brier:             {best_brier:.6f}")
    improvement = (1 - best_brier / min(brier_xgb, brier_lgb, brier_poi)) * 100
    print(f"  Improvement:                       {improvement:+.2f}%")
    print(f"{'='*60}")

    return {
        "weights": {"w_xgb": w_xgb, "w_lgb": w_lgb, "w_poi": w_poi},
        "brier_score": best_brier,
        "n_trials": n_trials,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Optuna Ensemble Weight Optimizer")
    parser.add_argument("--n-trials", type=int, default=100, help="Number of Optuna trials")
    parser.add_argument("--n-folds", type=int, default=5, help="Number of CV folds")
    args = parser.parse_args()

    run_optimization(n_trials=args.n_trials, n_folds=args.n_folds)
