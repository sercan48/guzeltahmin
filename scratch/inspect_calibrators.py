import sys
import pickle
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from config.settings import MODELS_DIR

for league in ["NORWAY_ELITESERIEN", "BRAZIL_SERIE_A"]:
    cal_path = MODELS_DIR / "ensemble" / f"calibrator_{league}.pkl"
    if cal_path.exists():
        with open(cal_path, "rb") as f:
            cal = pickle.load(f)
        print(f"League: {league}")
        print(f"  Calibrator type: {type(cal)}")
        # Print all attributes of the calibrator
        for attr in dir(cal):
            if not attr.startswith("__"):
                val = getattr(cal, attr)
                if not callable(val):
                    print(f"    {attr}: {val}")
