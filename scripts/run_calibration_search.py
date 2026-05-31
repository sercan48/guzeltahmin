"""Probability Calibration Lab Search.

Runs cross-validation across summer leagues to evaluate calibration techniques
and saves the best calibrators to disk.
"""

import sys
import pickle
import numpy as np
import pandas as pd
from pathlib import Path
from sklearn.model_selection import KFold

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from config.settings import MODELS_DIR, RANDOM_SEED
from config.constants import FEATURE_COLUMNS, LABEL_MAP
from src.model.ensemble import StackingEnsemble
from src.model.calibration_lab import PlattCalibrator, IsotonicCalibrator, BetaCalibrator, TemperatureScaler

def calculate_ece(predictions: np.ndarray, actuals: np.ndarray, n_bins: int = 10) -> float:
    """Expected Calibration Error (ECE)."""
    pred_probs = np.max(predictions, axis=1)
    pred_classes = np.argmax(predictions, axis=1)
    true_classes = actuals
    correct_preds = (pred_classes == true_classes)

    ece = 0.0
    n_samples = len(predictions)
    bin_edges = np.linspace(0, 1, n_bins + 1)

    for i in range(n_bins):
        bin_lower = bin_edges[i]
        bin_upper = bin_edges[i+1]
        in_bin = (pred_probs >= bin_lower) & (pred_probs < bin_upper)
        prop_in_bin = np.mean(in_bin)

        if prop_in_bin > 0:
            accuracy_in_bin = np.mean(correct_preds[in_bin])
            avg_confidence_in_bin = np.mean(pred_probs[in_bin])
            ece += prop_in_bin * np.abs(avg_confidence_in_bin - accuracy_in_bin)

    return ece

def evaluate_calibrator(calibrator_class, X_prob: np.ndarray, y: np.ndarray, n_splits: int = 5) -> dict:
    """Evaluate a calibrator using K-Fold cross-validation."""
    kf = KFold(n_splits=n_splits, shuffle=True, random_state=RANDOM_SEED)
    
    eces = []
    briers = []
    loglosses = []
    
    for train_idx, val_idx in kf.split(X_prob):
        X_tr, X_val = X_prob[train_idx], X_prob[val_idx]
        y_tr, y_val = y[train_idx], y[val_idx]
        
        cal = calibrator_class()
        cal.fit(X_tr, y_tr)
        preds = cal.predict(X_val)
        
        # Clip
        preds = np.clip(preds, 1e-15, 1.0 - 1e-15)
        
        # Metrics
        # 1. ECE
        eces.append(calculate_ece(preds, y_val))
        
        # 2. Brier Score
        y_onehot = np.zeros_like(preds)
        for i, val in enumerate(y_val):
            y_onehot[i, val] = 1.0
        briers.append(np.mean(np.sum((preds - y_onehot) ** 2, axis=1)))
        
        # 3. Log Loss
        loss = -np.mean(np.sum(y_onehot * np.log(preds), axis=1))
        loglosses.append(loss)
        
    return {
        "ece": np.mean(eces),
        "brier": np.mean(briers),
        "log_loss": np.mean(loglosses)
    }

def main():
    print("=" * 60)
    print("  PROBABILITY CALIBRATION LAB SEARCH")
    print("=" * 60)
    
    # Load feature matrix (dataset_v2)
    features_path = Path("data/processed/features.csv")
    if not features_path.exists():
        print(f"[ERROR] Features file not found at {features_path}")
        return
        
    df = pd.read_csv(features_path)
    df = df[df["ft_result"].isin(["H", "D", "A"])].reset_index(drop=True)
    
    # Load trained StackingEnsemble
    ensemble = StackingEnsemble()
    ensemble.load(MODELS_DIR / "ensemble")
    
    # Prepare features
    available = [c for c in FEATURE_COLUMNS if c in df.columns]
    X_all = df[available].fillna(0).values
    
    # Use OOF predictions from ensemble if available to avoid data leakage / overfitting
    if hasattr(ensemble, "oof_ensemble_probs") and ensemble.oof_ensemble_probs is not None:
        print("Using out-of-fold (OOF) predictions from ensemble...")
        raw_probs = ensemble.oof_ensemble_probs
    else:
        print("Generating raw meta-ensemble probability predictions (warning: may lead to overfitting)...")
        raw_probs = ensemble.predict_proba(X_all)
    
    leagues = ["NORWAY_ELITESERIEN", "BRAZIL_SERIE_A"]
    calibrators = {
        "Platt Scaling": PlattCalibrator,
        "Isotonic Regression": IsotonicCalibrator,
        "Beta Calibration": BetaCalibrator,
        "Temperature Scaling": TemperatureScaler
    }
    
    report_lines = [
        "# Calibration Search Report\n",
        "| League | Method | ECE | Brier Score | Log Loss | Composite Score |",
        "|---|---|---|---|---|---|",
    ]
    
    for league in leagues:
        idx = df[df["league_code"] == league].index.values
        if len(idx) < 15:
            print(f"\nSkipping {league} (insufficient matches: {len(idx)})")
            continue
            
        print(f"\n--- Running Calibration Lab for {league} ({len(idx)} matches) ---")
        
        X_league = raw_probs[idx]
        y_league = df.loc[idx, "ft_result"].map(LABEL_MAP).astype(int).values
        
        # Evaluate raw uncalibrated baseline
        y_onehot = np.zeros_like(X_league)
        for i, val in enumerate(y_league):
            y_onehot[i, val] = 1.0
        raw_ece = calculate_ece(X_league, y_league)
        raw_brier = np.mean(np.sum((X_league - y_onehot) ** 2, axis=1))
        raw_log_loss = -np.mean(np.sum(y_onehot * np.log(np.clip(X_league, 1e-15, 1.0 - 1e-15)), axis=1))
        raw_composite = raw_ece + raw_brier + raw_log_loss
        
        print(f"Raw Model (Uncalibrated): ECE={raw_ece:.4f} | Brier={raw_brier:.4f} | LogLoss={raw_log_loss:.4f} | Composite={raw_composite:.4f}")
        report_lines.append(f"| {league} | Raw Model (Uncalibrated) | {raw_ece:.4f} | {raw_brier:.4f} | {raw_log_loss:.4f} | {raw_composite:.4f} |")
        
        best_composite = raw_composite
        best_method = "Raw"
        best_calibrator_class = None
        
        for name, cal_class in calibrators.items():
            metrics = evaluate_calibrator(cal_class, X_league, y_league)
            composite = metrics["ece"] + metrics["brier"] + metrics["log_loss"]
            print(f"{name}: ECE={metrics['ece']:.4f} | Brier={metrics['brier']:.4f} | LogLoss={metrics['log_loss']:.4f} | Composite={composite:.4f}")
            report_lines.append(f"| {league} | {name} | {metrics['ece']:.4f} | {metrics['brier']:.4f} | {metrics['log_loss']:.4f} | {composite:.4f} |")
            
            if composite < best_composite:
                best_composite = composite
                best_method = name
                best_calibrator_class = cal_class
                
        print(f"Best Calibration Method for {league}: {best_method} (Composite: {best_composite:.4f})")
        
        # Fit and save the best calibrator on all matches of this league
        if best_calibrator_class is not None:
            best_calibrator = best_calibrator_class()
            best_calibrator.fit(X_league, y_league)
            
            # Save calibrator pickle
            cal_file = MODELS_DIR / "ensemble" / f"calibrator_{league}.pkl"
            with open(cal_file, "wb") as f:
                pickle.dump(best_calibrator, f)
            print(f"[OK] Saved best calibrator to {cal_file}")
            
    # Save search report
    output_path = Path("scratch/calibration_search_report.md")
    output_path.write_text("\n".join(report_lines), encoding="utf-8")
    print(f"\n[OK] Calibration Search Report saved to {output_path}")

if __name__ == "__main__":
    main()
