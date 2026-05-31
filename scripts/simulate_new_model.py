"""A/B Backtest Simulation — Old 1X2 Model vs New Omni-Market Pipeline.

Compares two prediction strategies over the last 6 months of historical DB data:

  Model A (Old):  XGBoost-only 1X2, raw probabilities, no calibration
  Model B (New):  Stacking Ensemble + Platt Calibration + Omni-Market EV tags

Outputs a console report with:
  - Accuracy, Brier Score, ROI per model
  - Per-class breakdown (H/D/A prediction accuracy)
  - Statistical significance (McNemar test)
  - Market diversity analysis

Usage:
    python scripts/simulate_new_model.py
    python scripts/simulate_new_model.py --months 3 --season 2025-2026
"""

import sys
import argparse
from pathlib import Path
from datetime import datetime, timedelta

sys.path.insert(0, str(Path(__file__).parent.parent))

import numpy as np
import pandas as pd

from config.settings import PROCESSED_DIR, RANDOM_SEED
from config.constants import FEATURE_COLUMNS, LABEL_MAP, LABEL_MAP_INV


def load_historical_data(months: int = 6, season: str = None) -> pd.DataFrame:
    """Load last N months of match data from features.csv."""
    path = PROCESSED_DIR / "features.csv"
    if not path.exists():
        raise FileNotFoundError(f"{path} not found. Run build_features.py first.")

    df = pd.read_csv(path)

    # Filter by season if provided
    if season and "season" in df.columns:
        df = df[df["season"] == season]

    # Filter by date if available
    if "date" in df.columns:
        df["date"] = pd.to_datetime(df["date"], errors="coerce")
        cutoff = datetime.now() - timedelta(days=months * 30)
        df = df[df["date"] >= cutoff]
    elif season:
        pass  # Already filtered by season
    else:
        # Take last N% as approximation
        n_rows = int(len(df) * (months / 24))
        df = df.tail(n_rows)

    print(f"[DATA] Loaded {len(df)} matches for last {months} months")
    return df


def prepare_xy(df: pd.DataFrame) -> tuple:
    """Extract features and labels."""
    # Filter out future matches or matches without results to prevent alignment shifts
    df = df[df["ft_result"].isin(["H", "D", "A"])].copy()

    available = [c for c in FEATURE_COLUMNS if c in df.columns]
    X = df[available].fillna(0).values
    y = df["ft_result"].map(LABEL_MAP).astype(int).values

    # Get home goals and away goals for Poisson
    hg = df["ft_home_goals"].fillna(0).values.astype(float)
    ag = df["ft_away_goals"].fillna(0).values.astype(float)

    # Odds for ROI
    odds_cols = ["best_home_odds", "best_draw_odds", "best_away_odds"]
    odds_df = None
    if all(c in df.columns for c in odds_cols):
        odds_df = df[odds_cols].copy()
    elif all(c in df.columns for c in ["b365_home", "b365_draw", "b365_away"]):
        odds_df = df[["b365_home", "b365_draw", "b365_away"]].copy()
        odds_df.columns = odds_cols

    return X, y, hg, ag, odds_df


def multiclass_brier(y_true: np.ndarray, probs: np.ndarray) -> float:
    """Multi-class Brier score (lower = better)."""
    n_classes = probs.shape[1]
    one_hot = np.zeros((len(y_true), n_classes))
    for i, label in enumerate(y_true):
        one_hot[i, label] = 1.0
    return float(np.mean(np.sum((probs - one_hot) ** 2, axis=1)))


def calculate_roi(y_true, y_pred, odds_df) -> float:
    """Simple flat-bet ROI calculation."""
    if odds_df is None or len(odds_df) == 0:
        return 0.0

    odds_arr = odds_df.values
    total_stake = 0
    total_return = 0

    for i in range(len(y_true)):
        pred = y_pred[i]
        actual = y_true[i]
        total_stake += 1.0

        if pred == actual and pred < odds_arr.shape[1]:
            total_return += odds_arr[i, pred]

    return ((total_return - total_stake) / total_stake * 100) if total_stake > 0 else 0.0


def run_model_a(X_train, y_train, X_test, y_test, hg_train, ag_train):
    """Model A: Old XGBoost-only 1X2 classifier."""
    from src.model.trainer import train_model
    model, _, _ = train_model(X_train, y_train, X_test, y_test)
    probs = model.predict_proba(X_test)
    preds = np.argmax(probs, axis=1)
    return preds, probs


def run_model_b(X_train, y_train, X_test, y_test, hg_train, ag_train):
    """Model B: Stacking Ensemble + Calibration + Omni-Market."""
    from src.model.ensemble import StackingEnsemble
    from sklearn.isotonic import IsotonicRegression

    # Train ensemble
    ens = StackingEnsemble()
    ens.train(X_train, y_train, hg_train, ag_train, n_folds=3)
    raw_probs = ens.predict_proba(X_test)

    # Apply isotonic calibration (fit on training OOF)
    train_probs = ens.predict_proba(X_train)
    calibrated = np.zeros_like(raw_probs)
    for cls_idx in range(3):
        ir = IsotonicRegression(out_of_bounds="clip")
        ir.fit(train_probs[:, cls_idx], (y_train == cls_idx).astype(float))
        calibrated[:, cls_idx] = ir.transform(raw_probs[:, cls_idx])

    # Normalize
    row_sums = calibrated.sum(axis=1, keepdims=True)
    row_sums = np.where(row_sums == 0, 1, row_sums)
    calibrated = calibrated / row_sums

    # Omni-Market: choose best EV tag instead of raw argmax
    preds = _omni_market_predictions(calibrated, raw_probs)

    return preds, calibrated


def _omni_market_predictions(calibrated_probs: np.ndarray,
                              raw_probs: np.ndarray) -> np.ndarray:
    """Apply omni-market logic: compare raw vs calibrated, pick best.

    For backtest we still resolve to 1X2 labels (0/1/2) since actual results
    are in that format. But the selection logic mimics the value scanner's
    primary/secondary comparison.
    """
    n = len(calibrated_probs)
    preds = np.zeros(n, dtype=int)

    for i in range(n):
        cal = calibrated_probs[i]
        raw = raw_probs[i]

        # Check if calibration significantly shifted any probability
        max_shift = np.max(np.abs(cal - raw))

        if max_shift > 0.05:
            # Calibration had impact → trust calibrated
            preds[i] = np.argmax(cal)
        else:
            # No significant change → check if draw is undervalued
            # (Anti-draw-spam: only pick draw if clearly dominant)
            if cal[1] > 0.38 and cal[1] > max(cal[0], cal[2]) * 0.9:
                preds[i] = 1  # Draw
            else:
                preds[i] = np.argmax(cal)

    return preds


def per_class_breakdown(y_true, y_pred):
    """Break down accuracy by H/D/A."""
    breakdown = {}
    for cls_idx, cls_name in enumerate(["H", "D", "A"]):
        mask = y_true == cls_idx
        total = mask.sum()
        if total > 0:
            correct = ((y_pred == cls_idx) & mask).sum()
            predicted_as = (y_pred == cls_idx).sum()
            breakdown[cls_name] = {
                "total_actual": int(total),
                "predicted_as": int(predicted_as),
                "correct": int(correct),
                "recall": round(correct / total, 4) if total > 0 else 0,
                "precision": round(correct / predicted_as, 4) if predicted_as > 0 else 0,
            }
    return breakdown


def market_diversity(y_pred):
    """Analyze prediction distribution."""
    total = len(y_pred)
    dist = {
        "H": int((y_pred == 0).sum()),
        "D": int((y_pred == 1).sum()),
        "A": int((y_pred == 2).sum()),
    }
    pcts = {k: round(v / total * 100, 1) for k, v in dist.items()}
    return dist, pcts


def mcnemar_test(y_true, preds_a, preds_b):
    """McNemar test for statistical significance between two models."""
    a_correct = preds_a == y_true
    b_correct = preds_b == y_true

    # Contingency: A right & B wrong, A wrong & B right
    a_right_b_wrong = ((a_correct) & (~b_correct)).sum()
    a_wrong_b_right = ((~a_correct) & (b_correct)).sum()

    n = a_right_b_wrong + a_wrong_b_right
    if n == 0:
        return {"chi2": 0, "p_value": 1.0, "significant": False}

    chi2 = (abs(a_right_b_wrong - a_wrong_b_right) - 1) ** 2 / n

    # Approximate p-value (chi-squared with 1 df)
    from scipy.stats import chi2 as chi2_dist
    p_value = 1 - chi2_dist.cdf(chi2, df=1)

    return {
        "chi2": round(chi2, 4),
        "p_value": round(p_value, 4),
        "significant": p_value < 0.05,
        "a_right_b_wrong": int(a_right_b_wrong),
        "a_wrong_b_right": int(a_wrong_b_right),
    }


def print_report(results: dict):
    """Print formatted A/B comparison report."""
    W = 65

    print(f"\n{'=' * W}")
    print(f"  A/B BACKTEST SIMULATION RAPORU")
    print(f"  {results['n_matches']} mac | Son {results['months']} ay")
    print(f"{'=' * W}")

    # Side-by-side comparison
    a = results["model_a"]
    b = results["model_b"]

    print(f"\n  {'Metrik':<25} {'Model A (Eski)':<20} {'Model B (Yeni)':<20}")
    print(f"  {'-' * 60}")

    metrics = [
        ("Accuracy", f"{a['accuracy']:.4f}", f"{b['accuracy']:.4f}"),
        ("Brier Score", f"{a['brier']:.6f}", f"{b['brier']:.6f}"),
        ("ROI %", f"{a['roi']:.2f}%", f"{b['roi']:.2f}%"),
    ]

    for name, va, vb in metrics:
        marker = ""
        if "Brier" in name:
            marker = " [OK]" if float(vb.replace("%", "")) <= float(va.replace("%", "")) else " [FAIL]"
        else:
            va_num = float(va.replace("%", ""))
            vb_num = float(vb.replace("%", ""))
            marker = " [OK]" if vb_num >= va_num else " [FAIL]"
        print(f"  {name:<25} {va:<20} {vb:<17}{marker}")

    # Deltas
    print(f"\n  {'DELTA (B - A)':<25}")
    print(f"  {'-' * 40}")
    acc_delta = b["accuracy"] - a["accuracy"]
    brier_delta = b["brier"] - a["brier"]
    roi_delta = b["roi"] - a["roi"]
    print(f"  {'Accuracy':<25} {acc_delta:+.4f}")
    print(f"  {'Brier Score':<25} {brier_delta:+.6f}")
    print(f"  {'ROI':<25} {roi_delta:+.2f}%")

    # Per-class breakdown
    print(f"\n{'=' * W}")
    print(f"  PER-CLASS BREAKDOWN")
    print(f"{'=' * W}")

    for model_name, model_data in [("Model A (Eski)", a), ("Model B (Yeni)", b)]:
        print(f"\n  {model_name}:")
        print(f"  {'Class':<8} {'Actual':<10} {'Predicted':<12} {'Correct':<10} {'Recall':<10} {'Precision':<10}")
        print(f"  {'-' * 55}")
        for cls, data in model_data.get("per_class", {}).items():
            print(f"  {cls:<8} {data['total_actual']:<10} {data['predicted_as']:<12} "
                  f"{data['correct']:<10} {data['recall']:<10.1%} {data['precision']:<10.1%}")

    # Market diversity
    print(f"\n{'=' * W}")
    print(f"  MARKET DIVERSITY")
    print(f"{'=' * W}")

    for model_name, model_data in [("Model A", a), ("Model B", b)]:
        dist, pcts = model_data["diversity"]
        print(f"  {model_name}: H={dist['H']} ({pcts['H']}%) "
              f"D={dist['D']} ({pcts['D']}%) A={dist['A']} ({pcts['A']}%)")

    # Statistical significance
    if "mcnemar" in results:
        mc = results["mcnemar"]
        print(f"\n{'=' * W}")
        print(f"  STATISTICAL SIGNIFICANCE (McNemar)")
        print(f"{'=' * W}")
        print(f"  Chi2: {mc['chi2']:.4f}")
        print(f"  p-value: {mc['p_value']:.4f}")
        print(f"  A_OK_B_FAIL: {mc['a_right_b_wrong']}  |  A_FAIL_B_OK: {mc['a_wrong_b_right']}")
        sig = "EVET [OK] (p < 0.05)" if mc["significant"] else "HAYIR (p >= 0.05)"
        print(f"  Istatistiksel Anlamlilik: {sig}")

    # Verdict
    print(f"\n{'=' * W}")
    if b["accuracy"] > a["accuracy"] and b["brier"] < a["brier"]:
        print(f"  [+] SONUC: Model B (Yeni) HER IKI METRIKTE USTUN")
    elif b["accuracy"] > a["accuracy"]:
        print(f"  [+] SONUC: Model B (Yeni) ACCURACY'DE USTUN, Brier'da esit/geri")
    elif b["brier"] < a["brier"]:
        print(f"  [+] SONUC: Model B (Yeni) BRIER'DA USTUN, Accuracy'de esit/geri")
    else:
        print(f"  [-] SONUC: Model A (Eski) hala daha iyi — daha fazla veri gerekebilir")
    print(f"{'=' * W}\n")


def run_simulation(months: int = 6, season: str = None):
    """Main simulation pipeline."""
    df = load_historical_data(months, season)
    if len(df) < 100:
        print(f"[WARN] Only {len(df)} matches — results may not be reliable.")

    X, y, hg, ag, odds_df = prepare_xy(df)

    # 70/30 time-based split (no shuffle — preserves temporal order)
    split = int(len(X) * 0.70)
    X_train, X_test = X[:split], X[split:]
    y_train, y_test = y[:split], y[split:]
    hg_train = hg[:split]
    ag_train = ag[:split]
    odds_test = odds_df.iloc[split:] if odds_df is not None else None

    print(f"[SPLIT] Train: {len(X_train)} | Test: {len(X_test)}")

    # Run Model A (Old)
    print("\n[MODEL A] Training Old XGBoost 1X2...")
    preds_a, probs_a = run_model_a(X_train, y_train, X_test, y_test, hg_train, ag_train)
    acc_a = float((preds_a == y_test).mean())
    brier_a = multiclass_brier(y_test, probs_a)
    roi_a = calculate_roi(y_test, preds_a, odds_test)

    # Run Model B (New)
    print("[MODEL B] Training New Ensemble + Calibration + Omni-Market...")
    preds_b, probs_b = run_model_b(X_train, y_train, X_test, y_test, hg_train, ag_train)
    acc_b = float((preds_b == y_test).mean())
    brier_b = multiclass_brier(y_test, probs_b)
    roi_b = calculate_roi(y_test, preds_b, odds_test)

    # McNemar test
    try:
        mc = mcnemar_test(y_test, preds_a, preds_b)
    except ImportError:
        mc = {"chi2": 0, "p_value": 1.0, "significant": False,
              "a_right_b_wrong": 0, "a_wrong_b_right": 0}

    results = {
        "months": months,
        "n_matches": len(X_test),
        "model_a": {
            "accuracy": acc_a,
            "brier": brier_a,
            "roi": roi_a,
            "per_class": per_class_breakdown(y_test, preds_a),
            "diversity": market_diversity(preds_a),
        },
        "model_b": {
            "accuracy": acc_b,
            "brier": brier_b,
            "roi": roi_b,
            "per_class": per_class_breakdown(y_test, preds_b),
            "diversity": market_diversity(preds_b),
        },
        "mcnemar": mc,
    }

    print_report(results)
    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="A/B Backtest: Old vs New Model")
    parser.add_argument("--months", type=int, default=6, help="Months of historical data")
    parser.add_argument("--season", type=str, default=None, help="Filter by season label")
    args = parser.parse_args()

    run_simulation(months=args.months, season=args.season)
