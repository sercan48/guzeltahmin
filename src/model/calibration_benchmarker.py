"""Calibration Benchmarking Framework.

Compares Platt Scaling, Isotonic Regression, and Beta Calibration per league
using Expected Calibration Error (ECE) as primary metric and Brier Score as secondary.
"""

import logging
import pickle
import json
from pathlib import Path
import numpy as np
from sklearn.model_selection import KFold
from sklearn.metrics import brier_score_loss

from config.settings import MODELS_DIR
from src.model.calibration_lab import PlattCalibrator, IsotonicCalibrator, BetaCalibrator

logger = logging.getLogger(__name__)


def calculate_multiclass_ece(probs: np.ndarray, y_true: np.ndarray, n_bins: int = 10) -> float:
    """Calculate Expected Calibration Error (ECE) for multi-class predictions."""
    pred_probs = np.max(probs, axis=1)
    pred_classes = np.argmax(probs, axis=1)
    correct_preds = (pred_classes == y_true)

    ece = 0.0
    n_samples = len(probs)
    if n_samples == 0:
        return 1.0

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


def calculate_multiclass_brier(probs: np.ndarray, y_true: np.ndarray) -> float:
    """Calculate average Brier score across classes (One-Vs-Rest)."""
    n_classes = probs.shape[1]
    total = 0.0
    for c in range(n_classes):
        y_binary = (y_true == c).astype(int)
        p_class = probs[:, c]
        total += brier_score_loss(y_binary, p_class)
    return total / n_classes


class CalibrationBenchmarker:
    """Benchmarks and selects the best calibration method per league based on ECE and Brier Score."""

    def __init__(self, models_dir: Path = None):
        self.models_dir = models_dir or MODELS_DIR / "ensemble"
        self.models_dir.mkdir(parents=True, exist_ok=True)

    def benchmark_and_save(self, league_code: str, raw_probs: np.ndarray, y: np.ndarray) -> str:
        """Runs 3-fold cross validation to evaluate Platt, Isotonic, and Beta calibration.

        Saves the best calibrator model and a metadata file recording the chosen method.
        """
        if len(raw_probs) < 15:
            logger.warning(f"Not enough samples to benchmark calibration for {league_code} ({len(raw_probs)} < 15). Skipping.")
            return "NONE"

        calibrators = {
            "Platt": PlattCalibrator,
            "Isotonic": IsotonicCalibrator,
            "Beta": BetaCalibrator
        }

        kf = KFold(n_splits=3, shuffle=True, random_state=42)
        scores = {name: {"ece": [], "brier": []} for name in calibrators}

        for train_idx, val_idx in kf.split(raw_probs):
            X_train, X_val = raw_probs[train_idx], raw_probs[val_idx]
            y_train, y_val = y[train_idx], y[val_idx]

            for name, CalibratorClass in calibrators.items():
                try:
                    cal = CalibratorClass()
                    cal.fit(X_train, y_train)
                    preds_val = cal.predict(X_val)

                    ece = calculate_multiclass_ece(preds_val, y_val)
                    brier = calculate_multiclass_brier(preds_val, y_val)

                    scores[name]["ece"].append(ece)
                    scores[name]["brier"].append(brier)
                except Exception as e:
                    logger.debug(f"Failed to evaluate {name} for {league_code} in CV fold: {e}")

        # Choose best calibrator based on average metrics
        best_name = "Platt"  # Default fallback
        best_ece = 999.0
        best_brier = 999.0

        for name in calibrators:
            avg_ece = np.mean(scores[name]["ece"]) if scores[name]["ece"] else 999.0
            avg_brier = np.mean(scores[name]["brier"]) if scores[name]["brier"] else 999.0

            logger.info(f"[Benchmark] {league_code} - {name}: ECE={avg_ece:.4f}, Brier={avg_brier:.4f}")

            # ECE is primary, Brier is secondary
            if avg_ece < best_ece - 0.005:  # significantly better ECE
                best_ece = avg_ece
                best_brier = avg_brier
                best_name = name
            elif abs(avg_ece - best_ece) <= 0.005:  # comparable ECE, check Brier score
                if avg_brier < best_brier:
                    best_ece = avg_ece
                    best_brier = avg_brier
                    best_name = name

        logger.info(f"[Benchmark Winner] {league_code} selected calibration: {best_name}")

        # Train chosen model on full data
        try:
            best_cal = calibrators[best_name]()
            best_cal.fit(raw_probs, y)

            # Save calibrator object
            model_path = self.models_dir / f"calibrator_{league_code}.pkl"
            with open(model_path, "wb") as f:
                pickle.dump(best_cal, f)

            # Save metadata about selection
            meta_path = self.models_dir / f"calibrator_{league_code}_meta.json"
            with open(meta_path, "w") as f:
                json.dump({
                    "league_code": league_code,
                    "best_method": best_name,
                    "cv_ece": float(best_ece),
                    "cv_brier": float(best_brier)
                }, f, indent=2)

            return best_name
        except Exception as e:
            logger.error(f"Failed to fit/save best calibrator {best_name} for {league_code}: {e}")
            return "NONE"
