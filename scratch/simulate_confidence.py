import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import numpy as np
import pandas as pd
import warnings
warnings.filterwarnings("ignore")

from config.settings import PROCESSED_DIR
from config.constants import FEATURE_COLUMNS, LABEL_MAP

# 1. Load data
path = PROCESSED_DIR / "features.csv"
df = pd.read_csv(path)
df = df[df["ft_result"].isin(["H", "D", "A"])].copy()

# Temporal split (70/30)
split = int(len(df) * 0.70)
train_df = df.iloc[:split]
test_df = df.iloc[split:]

# Prepare features and labels
available = [c for c in FEATURE_COLUMNS if c in df.columns]
X_train = train_df[available].fillna(0).values
y_train = train_df["ft_result"].map(LABEL_MAP).values
X_test = test_df[available].fillna(0).values
y_test = test_df["ft_result"].map(LABEL_MAP).values

# 2. Model A: Old XGBoost-only (uncalibrated)
from src.model.trainer import train_model as train_xgb
xgb_model, _, _ = train_xgb(X_train, y_train, X_test, y_test)
probs_a = xgb_model.predict_proba(X_test)

# 3. Model B: Calibrated Ensemble
from src.model.ensemble import StackingEnsemble
from sklearn.isotonic import IsotonicRegression

ens = StackingEnsemble()
ens.train(X_train, y_train, train_df["ft_home_goals"].fillna(0).values, train_df["ft_away_goals"].fillna(0).values, n_folds=3)
raw_probs_b = ens.predict_proba(X_test)

# Calibrate
train_probs = ens.predict_proba(X_train)
calibrated_b = np.zeros_like(raw_probs_b)
for cls_idx in range(3):
    ir = IsotonicRegression(out_of_bounds="clip")
    ir.fit(train_probs[:, cls_idx], (y_train == cls_idx).astype(float))
    calibrated_b[:, cls_idx] = ir.transform(raw_probs_b[:, cls_idx])

row_sums = calibrated_b.sum(axis=1, keepdims=True)
row_sums = np.where(row_sums == 0, 1, row_sums)
calibrated_b = calibrated_b / row_sums

# Simulate average missing player data for Model B (1.0 poisson mean)
np.random.seed(42)
home_missing_arr = np.random.poisson(lam=1.0, size=len(test_df))
away_missing_arr = np.random.poisson(lam=1.0, size=len(test_df))

# 4. Compute confidence scores for both models
from src.model.predictor import _calculate_confidence

conf_a_list = []
conf_b_list = []

for i in range(len(test_df)):
    row = test_df.iloc[i].to_dict()
    
    # Model A: Raw XGBoost probs, no ensemble agreement (1.0), no missing players
    prob_a = probs_a[i]
    max_a = np.max(prob_a)
    conf_a = _calculate_confidence(
        max_prob=max_a,
        model_agreement=1.0,
        features=row,
        h_prob=prob_a[0],
        d_prob=prob_a[1],
        a_prob=prob_a[2],
        home_missing=0,
        away_missing=0
    )
    conf_a_list.append(conf_a)
    
    # Model B: Calibrated ensemble probs, meta-model agreement, with missing players
    agreement = 0.95
    prob_b = calibrated_b[i]
    max_b = np.max(prob_b)
    
    conf_b = _calculate_confidence(
        max_prob=max_b,
        model_agreement=agreement,
        features=row,
        h_prob=prob_b[0],
        d_prob=prob_b[1],
        a_prob=prob_b[2],
        home_missing=home_missing_arr[i],
        away_missing=away_missing_arr[i]
    )
    conf_b_list.append(conf_b)

conf_a_arr = np.array(conf_a_list)
conf_b_arr = np.array(conf_b_list)

# 5. Output report
print("\n=== CONFIDENCE COMPARISON RAPORU ===")
print(f"Model A (Eski) Ortalama Guven: {conf_a_arr.mean():.2f}")
print(f"Model B (Yeni) Ortalama Guven: {conf_b_arr.mean():.2f}")

print("\n=== GUVEN ARALIGI DAGILIMI ===")
for lower, upper in [(1, 5), (5, 7), (7, 8.5), (8.5, 10)]:
    count_a = ((conf_a_arr >= lower) & (conf_a_arr < upper)).sum() if upper < 10 else ((conf_a_arr >= lower) & (conf_a_arr <= upper)).sum()
    count_b = ((conf_b_arr >= lower) & (conf_b_arr < upper)).sum() if upper < 10 else ((conf_b_arr >= lower) & (conf_b_arr <= upper)).sum()
    pct_a = count_a / len(test_df) * 100
    pct_b = count_b / len(test_df) * 100
    print(f"  [{lower} - {upper}]: Model A: {count_a} ({pct_a:.1f}%) | Model B: {count_b} ({pct_b:.1f}%)")

print("\n=== YUKSEK GUVENLI (>= 8.0) MACLARIN ACURACY VE BRIER DEGERLERI ===")
high_a_mask = conf_a_arr >= 8.0
high_b_mask = conf_b_arr >= 8.0

preds_a = np.argmax(probs_a, axis=1)
preds_b = np.argmax(calibrated_b, axis=1)

acc_high_a = (preds_a[high_a_mask] == y_test[high_a_mask]).mean() if high_a_mask.sum() > 0 else 0
acc_high_b = (preds_b[high_b_mask] == y_test[high_b_mask]).mean() if high_b_mask.sum() > 0 else 0

print(f"Model A (Eski): Toplam {high_a_mask.sum()} mac (Guven >= 8.0) -> Dogruluk: {acc_high_a:.2%}")
print(f"Model B (Yeni): Toplam {high_b_mask.sum()} mac (Guven >= 8.0) -> Dogruluk: {acc_high_b:.2%}")
