"""XGBoost model training pipeline."""

import pickle
from pathlib import Path

import numpy as np
import pandas as pd
import xgboost as xgb
from sklearn.model_selection import train_test_split
from sklearn.metrics import accuracy_score, classification_report

from config.settings import MODEL_PATH, RANDOM_SEED, TEST_SIZE
from config.constants import FEATURE_COLUMNS, LABEL_MAP, LABEL_MAP_INV


def prepare_training_data(features_df: pd.DataFrame) -> tuple:
    """Split features DataFrame into X, y for training.

    Args:
        features_df: DataFrame with feature columns and 'ft_result' target

    Returns:
        X_train, X_test, y_train, y_test
    """
    available = [c for c in FEATURE_COLUMNS if c in features_df.columns]
    if not available:
        raise ValueError("No feature columns found in DataFrame!")

    X = features_df[available].fillna(0).values
    y = features_df["ft_result"].map(LABEL_MAP).values

    # Remove rows where label is NaN
    valid_mask = ~np.isnan(y)
    X = X[valid_mask]
    y = y[valid_mask].astype(int)

    return train_test_split(X, y, test_size=TEST_SIZE, random_state=RANDOM_SEED, stratify=y)


def train_model(
    X_train: np.ndarray,
    y_train: np.ndarray,
    X_test: np.ndarray,
    y_test: np.ndarray,
    params: dict = None,
) -> tuple:
    """Train XGBoost multiclass classifier.

    Returns:
        (model, accuracy, report_dict)
    """
    if params is None:
        params = {
            "objective": "multi:softprob",
            "num_class": 3,
            "max_depth": 7,
            "learning_rate": 0.08,
            "n_estimators": 500,
            "subsample": 0.75,
            "colsample_bytree": 0.75,
            "min_child_weight": 3,
            "gamma": 0.15,
            "reg_alpha": 0.5,
            "reg_lambda": 1.5,
            "random_state": RANDOM_SEED,
            "eval_metric": "mlogloss",
            "early_stopping_rounds": 30,
        }

    # Extract XGBClassifier params (copy to avoid mutation)
    params = dict(params)
    n_estimators = params.pop("n_estimators", 300)
    early_stopping = params.pop("early_stopping_rounds", 30)

    model = xgb.XGBClassifier(
        n_estimators=n_estimators,
        early_stopping_rounds=early_stopping,
        **params,
    )

    model.fit(
        X_train, y_train,
        eval_set=[(X_test, y_test)],
        verbose=False,
    )

    y_pred = model.predict(X_test)
    accuracy = accuracy_score(y_test, y_pred)
    report = classification_report(
        y_test, y_pred,
        target_names=["Home", "Draw", "Away"],
        output_dict=True,
    )

    print(f"\n[MODEL] Accuracy: {accuracy:.4f}")
    print(classification_report(y_test, y_pred, target_names=["Home", "Draw", "Away"]))

    return model, accuracy, report


def save_model(model: xgb.XGBClassifier, path: Path = None):
    """Save trained model to disk."""
    path = path or MODEL_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as f:
        pickle.dump(model, f)
    print(f"[OK] Model saved to {path}")


def load_model(path: Path = None) -> xgb.XGBClassifier:
    """Load trained model from disk."""
    path = path or MODEL_PATH
    if not path.exists():
        raise FileNotFoundError(f"Model not found at {path}. Train first!")
    with open(path, "rb") as f:
        return pickle.load(f)


def feature_importance(model: xgb.XGBClassifier) -> dict[str, float]:
    """Get feature importance scores."""
    importance = model.feature_importances_
    names = FEATURE_COLUMNS[:len(importance)]
    return dict(sorted(zip(names, importance), key=lambda x: x[1], reverse=True))


def full_training_pipeline(features_df: pd.DataFrame, params: dict = None) -> tuple:
    """End-to-end: split → train → save → report.

    Returns:
        (model, accuracy, report)
    """
    print("=" * 60)
    print("  XGBoost Training Pipeline")
    print("=" * 60)

    X_train, X_test, y_train, y_test = prepare_training_data(features_df)
    print(f"[DATA] Train: {len(X_train)}, Test: {len(X_test)}")

    model, accuracy, report = train_model(X_train, y_train, X_test, y_test, params)
    save_model(model)

    fi = feature_importance(model)
    print("\n[TOP FEATURES]")
    for name, imp in list(fi.items())[:10]:
        print(f"  {name}: {imp:.4f}")

    return model, accuracy, report
