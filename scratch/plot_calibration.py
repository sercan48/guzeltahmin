import sys
import numpy as np
import pandas as pd
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")
sys.path.insert(0, str(Path(__file__).parent.parent))

from config.settings import PROCESSED_DIR
from config.constants import FEATURE_COLUMNS, LABEL_MAP

def compute_calibration_stats(y_true, probs):
    """Compute reliability curve stats manually for decision confidence."""
    preds = np.argmax(probs, axis=1)
    confidences = np.max(probs, axis=1)
    corrects = (preds == y_true).astype(float)
    
    bins = [0.0, 0.2, 0.4, 0.6, 0.8, 0.9, 1.0]
    bin_labels = ["0-20%", "20-40%", "40-60%", "60-80%", "80-90%", "90-100%"]
    
    stats = []
    for i in range(len(bins)-1):
        lower = bins[i]
        upper = bins[i+1]
        
        # Mask for samples in this bin
        if i == len(bins)-2:
            mask = (confidences >= lower) & (confidences <= upper)
        else:
            mask = (confidences >= lower) & (confidences < upper)
            
        count = int(mask.sum())
        if count > 0:
            mean_conf = float(confidences[mask].mean())
            accuracy = float(corrects[mask].mean())
        else:
            mean_conf = 0.0
            accuracy = 0.0
            
        stats.append({
            "range": bin_labels[i],
            "count": count,
            "mean_conf": mean_conf,
            "accuracy": accuracy
        })
    return stats

def main():
    path = PROCESSED_DIR / "features.csv"
    df = pd.read_csv(path)
    df = df[df["ft_result"].isin(["H", "D", "A"])].copy()

    split = int(len(df) * 0.70)
    train_df = df.iloc[:split]
    test_df = df.iloc[split:]

    # Split train_df into model training and calibration sets (80/20) to prevent leakage
    cal_split = int(len(train_df) * 0.80)
    model_df = train_df.iloc[:cal_split]
    cal_df = train_df.iloc[cal_split:]

    available = [c for c in FEATURE_COLUMNS if c in df.columns]
    X_model = model_df[available].fillna(0).values
    y_model = model_df["ft_result"].map(LABEL_MAP).values
    
    X_cal = cal_df[available].fillna(0).values
    y_cal = cal_df["ft_result"].map(LABEL_MAP).values

    X_test = test_df[available].fillna(0).values
    y_test = test_df["ft_result"].map(LABEL_MAP).values

    # Train ensemble on model training set
    from src.model.ensemble import StackingEnsemble
    from sklearn.isotonic import IsotonicRegression

    ens = StackingEnsemble()
    ens.train(X_model, y_model, model_df["ft_home_goals"].fillna(0).values, model_df["ft_away_goals"].fillna(0).values, n_folds=3)
    
    # 1. RAW probabilities
    probs_raw = ens.predict_proba(X_test)

    # Get predictions on calibration set (unseen by ensemble)
    cal_probs_pred = ens.predict_proba(X_cal)

    # 2. CALIBRATED probabilities
    probs_cal = np.zeros_like(probs_raw)
    for cls_idx in range(3):
        ir = IsotonicRegression(out_of_bounds="clip")
        ir.fit(cal_probs_pred[:, cls_idx], (y_cal == cls_idx).astype(float))
        probs_cal[:, cls_idx] = ir.transform(probs_raw[:, cls_idx])
    # Normalize
    row_sums = probs_cal.sum(axis=1, keepdims=True)
    probs_cal = probs_cal / np.where(row_sums == 0, 1, row_sums)

    # 3. FINAL probabilities (Calibrated + Temperature scaling T=1.15)
    # We apply logit transform, shift (0 since no team_status used), and Softmax with T=1.15
    # This matches the final pipeline output.
    from src.agents.data_agent import apply_agent_penalty
    probs_final = np.zeros_like(probs_cal)
    
    # Empty status dicts for zero shift
    empty_status = {"power_loss_pct": 0, "key_absences": []}
    
    for i in range(len(X_test)):
        h, d, a = apply_agent_penalty(
            probs_cal[i, 0], probs_cal[i, 1], probs_cal[i, 2],
            empty_status, empty_status, temperature=1.15
        )
        probs_final[i] = [h, d, a]

    # Compute stats
    raw_stats = compute_calibration_stats(y_test, probs_raw)
    cal_stats = compute_calibration_stats(y_test, probs_cal)
    final_stats = compute_calibration_stats(y_test, probs_final)

    print("\n" + "="*80)
    print("  CALIBRATION & RELIABILITY DIAGRAM DATA")
    print("="*80)
    print(f"  Test size: {len(X_test)} matches")
    print("="*80)
    
    # Format and print results
    header = f"  {'Bin':<10} | {'RAW Stage':<20} | {'CALIBRATED Stage':<20} | {'FINAL Stage (T=1.15)':<20}"
    divider = "  " + "-"*10 + "-+-" + "-"*20 + "-+-" + "-"*20 + "-+-" + "-"*20
    print(header)
    print(divider)
    
    for i in range(len(raw_stats)):
        r = raw_stats[i]
        c = cal_stats[i]
        f = final_stats[i]
        
        raw_str = f"{r['count']:>3} m | C:{r['mean_conf']:>5.1%} A:{r['accuracy']:>5.1%}"
        cal_str = f"{c['count']:>3} m | C:{c['mean_conf']:>5.1%} A:{c['accuracy']:>5.1%}"
        fin_str = f"{f['count']:>3} m | C:{f['mean_conf']:>5.1%} A:{f['accuracy']:>5.1%}"
        
        # Add warning tags for overconfidence (where predicted conf is > 5% higher than actual accuracy)
        raw_warn = " [!]" if r['mean_conf'] - r['accuracy'] > 0.05 else ""
        cal_warn = " [!]" if c['mean_conf'] - c['accuracy'] > 0.05 else ""
        fin_warn = " [!]" if f['mean_conf'] - f['accuracy'] > 0.05 else ""
        
        print(f"  {r['range']:<10} | {raw_str}{raw_warn:<5} | {cal_str}{cal_warn:<5} | {fin_str}{fin_warn:<5}")

    print(divider)
    print("  * Not: m = mac adedi, C = Ortalama Guven (Confidence), A = Gercek Isabet (Accuracy)")
    print("  * [!] = Asiri Guven (Overconfidence) Sinyali (Guven - Isabet > %5)")
    print("="*80 + "\n")

if __name__ == "__main__":
    main()
