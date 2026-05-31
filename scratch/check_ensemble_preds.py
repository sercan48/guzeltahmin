import sys
import pandas as pd
import numpy as np
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from config.settings import MODELS_DIR
from config.constants import FEATURE_COLUMNS, LABEL_MAP
from src.model.ensemble import StackingEnsemble

features_path = Path("data/processed/features.csv")
df = pd.read_csv(features_path)
df = df[df["ft_result"].isin(["H", "D", "A"])].reset_index(drop=True)

ensemble = StackingEnsemble()
ensemble.load(MODELS_DIR / "ensemble")

available = [c for c in FEATURE_COLUMNS if c in df.columns]
X_all = df[available].fillna(0).values
raw_probs = ensemble.predict_proba(X_all)

print("Raw probs shape:", raw_probs.shape)
print("Raw probs first 5 rows:\n", raw_probs[:5])
y = df["ft_result"].map(LABEL_MAP).astype(int).values
print("y first 5 rows:", y[:5])
print("Is there any mismatch or are probabilities already highly accurate or uncalibrated?")
