"""Confusion matrix and error analysis for model calibration."""

import numpy as np
import pandas as pd
from sklearn.metrics import confusion_matrix, classification_report


def analyze_errors(results: pd.DataFrame) -> dict:
    """Detailed error analysis of backtest results.

    Identifies:
    - Which result types are most confused
    - Error patterns by league
    - Error patterns by odds range
    - Calibration (predicted probability vs actual frequency)
    """
    y_true = results["ft_result"].values
    y_pred = results["predicted_result"].values

    # Confusion matrix
    labels = ["H", "D", "A"]
    cm = confusion_matrix(y_true, y_pred, labels=labels)

    report = classification_report(y_true, y_pred, labels=labels, output_dict=True)

    # Most common misclassifications
    errors = results[~results["correct"]].copy()
    error_patterns = {}
    for true_r in labels:
        for pred_r in labels:
            if true_r != pred_r:
                count = ((errors["ft_result"] == true_r) & (errors["predicted_result"] == pred_r)).sum()
                if count > 0:
                    error_patterns[f"{true_r}->{pred_r}"] = int(count)

    error_patterns = dict(sorted(error_patterns.items(), key=lambda x: x[1], reverse=True))

    return {
        "confusion_matrix": cm.tolist(),
        "classification_report": report,
        "error_patterns": error_patterns,
        "total_errors": len(errors),
        "error_rate": round(len(errors) / len(results), 4) if len(results) > 0 else 0,
    }


def calibration_analysis(results: pd.DataFrame, n_bins: int = 10) -> dict:
    """Check if predicted probabilities match actual outcome frequencies.

    A well-calibrated model: when it says 70%, the event happens ~70% of the time.
    """
    calibration = {}

    for outcome, prob_col in [
        ("H", "pred_home_prob"),
        ("D", "pred_draw_prob"),
        ("A", "pred_away_prob"),
    ]:
        if prob_col not in results.columns:
            continue

        probs = results[prob_col].values
        actuals = (results["ft_result"] == outcome).astype(int).values

        bins = np.linspace(0, 1, n_bins + 1)
        bin_data = []

        for i in range(n_bins):
            mask = (probs >= bins[i]) & (probs < bins[i + 1])
            if mask.sum() > 0:
                mean_pred = probs[mask].mean()
                mean_actual = actuals[mask].mean()
                bin_data.append({
                    "bin": f"{bins[i]:.1f}-{bins[i+1]:.1f}",
                    "count": int(mask.sum()),
                    "predicted": round(mean_pred, 3),
                    "actual": round(mean_actual, 3),
                    "gap": round(abs(mean_pred - mean_actual), 3),
                })

        calibration[outcome] = bin_data

    return calibration


def league_error_analysis(results: pd.DataFrame) -> dict:
    """Identify which leagues have highest/lowest accuracy."""
    if "league_code" not in results.columns:
        return {}

    league_stats = {}
    for league, group in results.groupby("league_code"):
        total = len(group)
        correct = group["correct"].sum()
        errors = group[~group["correct"]]

        # Most common error type in this league
        if len(errors) > 0:
            common_error = errors.groupby(["ft_result", "predicted_result"]).size()
            top_error = common_error.idxmax()
            error_desc = f"{top_error[0]}->{top_error[1]}"
        else:
            error_desc = "None"

        league_stats[league] = {
            "total": total,
            "accuracy": round(correct / total, 4) if total > 0 else 0,
            "most_common_error": error_desc,
        }

    return dict(sorted(league_stats.items(), key=lambda x: x[1]["accuracy"]))


def print_error_analysis(analysis: dict):
    """Pretty print error analysis results."""
    print("\n" + "=" * 60)
    print("  ERROR ANALYSIS")
    print("=" * 60)

    print(f"\n  Total Errors: {analysis['total_errors']}")
    print(f"  Error Rate: %{analysis['error_rate'] * 100:.1f}")

    print("\n  --- Top Error Patterns ---")
    for pattern, count in list(analysis["error_patterns"].items())[:5]:
        print(f"  {pattern}: {count} times")

    cm = analysis["confusion_matrix"]
    print("\n  --- Confusion Matrix ---")
    print(f"  {'':>10} Pred_H  Pred_D  Pred_A")
    for i, label in enumerate(["True_H", "True_D", "True_A"]):
        print(f"  {label:>10} {cm[i][0]:>6}  {cm[i][1]:>6}  {cm[i][2]:>6}")

    print("=" * 60)
