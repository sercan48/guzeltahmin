import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from config.settings import MODELS_DIR
from src.model.predictor import _load_calibrator, _calibrate_probs
import numpy as np

print("MODELS_DIR:", MODELS_DIR)
for league in ["NORWAY_ELITESERIEN", "BRAZIL_SERIE_A"]:
    cal = _load_calibrator(league)
    print(f"League: {league}, Calibrator: {cal}")
    if cal:
        raw_prob = np.array([[0.6, 0.2, 0.2]])
        cal_prob = _calibrate_probs(raw_prob, cal)
        print(f"  Raw: {raw_prob} -> Calibrated: {cal_prob}")
