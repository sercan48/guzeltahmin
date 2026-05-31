"""LightGBM multiclass classifier for H/D/A prediction."""

import pickle
import logging
from pathlib import Path

import numpy as np
import lightgbm as lgb
from sklearn.metrics import accuracy_score, classification_report

from config.settings import MODELS_DIR, RANDOM_SEED

logger = logging.getLogger(__name__)


class LightGBMTrainer:
    """LightGBM-based football match predictor."""

    def __init__(self, params: dict = None):
        self.params = params or {
            "objective": "multiclass",
            "num_class": 3,
            "metric": "multi_logloss",
            "num_leaves": 31,
            "max_depth": -1,
            "learning_rate": 0.05,
            "n_estimators": 500,
            "min_child_samples": 20,
            "subsample": 0.8,
            "colsample_bytree": 0.8,
            "reg_alpha": 0.3,
            "reg_lambda": 1.0,
            "verbose": -1,
            "random_state": RANDOM_SEED,
        }
        self.model = None

    def train(self, X_train: np.ndarray, y_train: np.ndarray,
              X_test: np.ndarray, y_test: np.ndarray) -> tuple:
        """Train LightGBM classifier.

        Returns: (model, accuracy, report_dict)
        """
        n_est = self.params.pop("n_estimators", 500)
        self.model = lgb.LGBMClassifier(n_estimators=n_est, **self.params)
        self.params["n_estimators"] = n_est  # restore

        self.model.fit(
            X_train, y_train,
            eval_set=[(X_test, y_test)],
            callbacks=[lgb.early_stopping(30, verbose=False), lgb.log_evaluation(0)],
        )

        y_pred = self.model.predict(X_test)
        acc = accuracy_score(y_test, y_pred)
        report = classification_report(
            y_test, y_pred,
            target_names=["Home", "Draw", "Away"],
            output_dict=True,
        )

        logger.info(f"[LightGBM] Accuracy: {acc:.4f}")
        return self.model, acc, report

    def predict_proba(self, X: np.ndarray) -> np.ndarray:
        if self.model is None:
            raise RuntimeError("Model not trained")
        return self.model.predict_proba(X)

    def predict(self, X: np.ndarray) -> np.ndarray:
        if self.model is None:
            raise RuntimeError("Model not trained")
        return self.model.predict(X)

    def save(self, path: Path = None):
        path = path or (MODELS_DIR / "lightgbm_latest.pkl")
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "wb") as f:
            pickle.dump(self.model, f)
        logger.info(f"LightGBM saved to {path}")

    def load(self, path: Path = None):
        path = path or (MODELS_DIR / "lightgbm_latest.pkl")
        with open(path, "rb") as f:
            self.model = pickle.load(f)
        logger.info(f"LightGBM loaded from {path}")
        return self.model

    def feature_importance(self, feature_names: list = None) -> dict:
        if self.model is None:
            return {}
        imp = self.model.feature_importances_
        names = feature_names or [f"f_{i}" for i in range(len(imp))]
        return dict(sorted(zip(names, imp), key=lambda x: x[1], reverse=True))
