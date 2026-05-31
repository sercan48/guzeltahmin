"""Unified metrics calculation engine."""

import numpy as np
import logging
from sklearn.metrics import accuracy_score, log_loss, brier_score_loss

from config.constants import METRIC_WEIGHTS

logger = logging.getLogger(__name__)


class MetricsEngine:
    """Calculate all prediction quality metrics."""

    def calculate_all(self, y_true: np.ndarray, y_pred: np.ndarray,
                      y_prob: np.ndarray, odds_df=None) -> dict:
        """Calculate all metrics at once.

        Args:
            y_true: actual labels (0, 1, 2)
            y_pred: predicted labels
            y_prob: probability matrix (n_samples, 3)
            odds_df: DataFrame with odds columns for ROI
        """
        acc = accuracy_score(y_true, y_pred)

        # Brier score (one-vs-all average)
        brier = self._multiclass_brier(y_true, y_prob)

        # Log-loss
        try:
            ll = log_loss(y_true, y_prob, labels=[0, 1, 2])
        except Exception:
            ll = 1.0

        # ROI (if odds available)
        roi = 0.0
        yield_pct = 0.0
        if odds_df is not None:
            try:
                from src.evaluator.roi_calculator import ROICalculator
                import pandas as pd
                calc = ROICalculator()
                label_inv = {0: "H", 1: "D", 2: "A"}

                roi_df = odds_df.copy()
                roi_df["predicted_result"] = [label_inv.get(p, "H") for p in y_pred]
                roi_df["actual_result"] = [label_inv.get(t, "H") for t in y_true]
                roi_df["pred_home_prob"] = y_prob[:, 0] if len(y_prob.shape) > 1 else 0
                roi_df["pred_draw_prob"] = y_prob[:, 1] if len(y_prob.shape) > 1 else 0
                roi_df["pred_away_prob"] = y_prob[:, 2] if len(y_prob.shape) > 1 else 0

                result = calc.calculate_flat_roi(roi_df)
                roi = result.get("roi_pct", 0)
                yield_pct = result.get("yield_pct", 0)
            except Exception as e:
                logger.warning(f"ROI calculation failed: {e}")

        composite = self.composite_score({
            "accuracy": acc, "roi": roi, "brier_score": brier,
            "log_loss": ll, "yield_pct": yield_pct,
        })

        return {
            "accuracy": round(acc, 4),
            "brier_score": round(brier, 4),
            "log_loss": round(ll, 4),
            "roi": round(roi, 2),
            "yield_pct": round(yield_pct, 2),
            "composite_score": round(composite, 4),
        }

    def composite_score(self, metrics: dict) -> float:
        """Weighted composite score."""
        acc = metrics.get("accuracy", 0)
        roi_norm = max(0, min((metrics.get("roi", 0) + 20) / 40, 1))
        brier_inv = 1 - min(metrics.get("brier_score", 1), 1)
        ll_inv = 1 - min(metrics.get("log_loss", 2) / 2, 1)
        yield_norm = max(0, min((metrics.get("yield_pct", 0) + 5) / 10, 1))

        w = METRIC_WEIGHTS
        return (
            w["accuracy"] * acc +
            w["roi"] * roi_norm +
            w["brier_score"] * brier_inv +
            w["log_loss"] * ll_inv +
            w["yield_pct"] * yield_norm
        )

    def _multiclass_brier(self, y_true: np.ndarray, y_prob: np.ndarray) -> float:
        """Average Brier score across all classes."""
        n_classes = y_prob.shape[1] if len(y_prob.shape) > 1 else 3
        total = 0.0
        for c in range(n_classes):
            y_binary = (y_true == c).astype(int)
            p_class = y_prob[:, c] if len(y_prob.shape) > 1 else np.zeros(len(y_true))
            total += brier_score_loss(y_binary, p_class)
        return total / n_classes

    def per_league_metrics(self, results_df) -> dict:
        """Break down metrics by league_code."""
        breakdown = {}
        if "league_code" not in results_df.columns:
            return breakdown

        for league, group in results_df.groupby("league_code"):
            correct = group.get("correct", group["predicted_result"] == group["ft_result"])
            breakdown[league] = {
                "accuracy": round(float(correct.mean()), 4),
                "matches": len(group),
            }
        return breakdown

    def per_result_metrics(self, results_df) -> dict:
        """Break down by result type (H, D, A)."""
        breakdown = {}
        actual_col = "ft_result" if "ft_result" in results_df.columns else "actual_result"

        for result in ["H", "D", "A"]:
            mask = results_df[actual_col] == result
            subset = results_df[mask]
            if len(subset) > 0:
                pred_correct = subset["predicted_result"] == subset[actual_col]
                breakdown[result] = {
                    "total": len(subset),
                    "correct": int(pred_correct.sum()),
                    "accuracy": round(float(pred_correct.mean()), 4),
                }
        return breakdown

    def calibration_curve(self, y_true: np.ndarray, y_prob: np.ndarray,
                           n_bins: int = 10) -> dict:
        """Reliability diagram data."""
        max_probs = np.max(y_prob, axis=1)
        correct = (np.argmax(y_prob, axis=1) == y_true)

        bin_edges = np.linspace(0, 1, n_bins + 1)
        bin_centers = []
        bin_accuracies = []
        bin_counts = []

        for i in range(n_bins):
            mask = (max_probs >= bin_edges[i]) & (max_probs < bin_edges[i + 1])
            count = mask.sum()
            if count > 0:
                bin_centers.append(round((bin_edges[i] + bin_edges[i + 1]) / 2, 2))
                bin_accuracies.append(round(float(correct[mask].mean()), 4))
                bin_counts.append(int(count))

        return {
            "bin_centers": bin_centers,
            "bin_accuracies": bin_accuracies,
            "bin_counts": bin_counts,
        }
