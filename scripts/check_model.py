import pickle
import sys

try:
    with open('models/ensemble/xgb.pkl', 'rb') as f:
        model = pickle.load(f)
    print("Features expected by XGB:", getattr(model, 'n_features_in_', 'Unknown'))
    if hasattr(model, 'feature_names_in_'):
        print("Feature names:", model.feature_names_in_)
except Exception as e:
    print("Error:", e)
