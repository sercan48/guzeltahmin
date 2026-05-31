import sys
sys.path.insert(0, '.')
from src.model.trainer import load_model
try:
    model = load_model()
    print("Features expected by XGB:", getattr(model, 'n_features_in_', 'Unknown'))
    if hasattr(model, 'feature_names_in_'):
        print("Feature names:", model.feature_names_in_)
except Exception as e:
    print("Error:", e)
