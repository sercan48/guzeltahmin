"""Probability Calibration Laboratory.

Implements Platt Scaling, Isotonic Regression, Beta Calibration, and Temperature Scaling
for multi-class probability calibration.
"""

import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.isotonic import IsotonicRegression
from scipy.optimize import minimize_scalar

class PlattCalibrator:
    """Platt Scaling (Logistic Calibration) for multi-class."""
    def __init__(self):
        self.models = {}

    def fit(self, probs: np.ndarray, y: np.ndarray):
        # Fit a binary logistic regression model for each class (One-Vs-Rest)
        n_classes = probs.shape[1]
        for c in range(n_classes):
            clf = LogisticRegression(C=1.0, max_iter=1000)
            y_bin = (y == c).astype(int)
            # Use raw probability as feature
            X = probs[:, c].reshape(-1, 1)
            # Add epsilon mapping to prevent infinities
            X = np.clip(X, 1e-15, 1 - 1e-15)
            clf.fit(X, y_bin)
            self.models[c] = clf
        return self

    def predict(self, probs: np.ndarray) -> np.ndarray:
        n_samples = probs.shape[0]
        n_classes = probs.shape[1]
        calibrated = np.zeros_like(probs)
        for c in range(n_classes):
            X = probs[:, c].reshape(-1, 1)
            X = np.clip(X, 1e-15, 1 - 1e-15)
            # Get probability of class 1 (occurred)
            calibrated[:, c] = self.models[c].predict_proba(X)[:, 1]
        
        # Re-normalize to sum to 1
        row_sums = calibrated.sum(axis=1, keepdims=True)
        row_sums = np.where(row_sums == 0, 1, row_sums)
        return calibrated / row_sums


class IsotonicCalibrator:
    """Isotonic Regression Calibration for multi-class."""
    def __init__(self):
        self.models = {}

    def fit(self, probs: np.ndarray, y: np.ndarray):
        n_classes = probs.shape[1]
        for c in range(n_classes):
            ir = IsotonicRegression(out_of_bounds='clip')
            y_bin = (y == c).astype(float)
            X = probs[:, c]
            ir.fit(X, y_bin)
            self.models[c] = ir
        return self

    def predict(self, probs: np.ndarray) -> np.ndarray:
        n_classes = probs.shape[1]
        calibrated = np.zeros_like(probs)
        for c in range(n_classes):
            X = probs[:, c]
            calibrated[:, c] = self.models[c].predict(X)
        
        # Re-normalize to sum to 1
        row_sums = calibrated.sum(axis=1, keepdims=True)
        row_sums = np.where(row_sums == 0, 1, row_sums)
        return calibrated / row_sums


class BetaCalibrator:
    """Beta Calibration based on Kull, Silva Filho and Flach (2017)."""
    def __init__(self):
        self.models = {}

    def fit(self, probs: np.ndarray, y: np.ndarray):
        n_classes = probs.shape[1]
        for c in range(n_classes):
            clf = LogisticRegression(C=1.0, max_iter=1000)
            y_bin = (y == c).astype(int)
            p = np.clip(probs[:, c], 1e-15, 1 - 1e-15)
            # Transform: [ln(p), -ln(1-p)]
            X_trans = np.column_stack([np.log(p), -np.log(1.0 - p)])
            clf.fit(X_trans, y_bin)
            self.models[c] = clf
        return self

    def predict(self, probs: np.ndarray) -> np.ndarray:
        n_classes = probs.shape[1]
        calibrated = np.zeros_like(probs)
        for c in range(n_classes):
            p = np.clip(probs[:, c], 1e-15, 1 - 1e-15)
            X_trans = np.column_stack([np.log(p), -np.log(1.0 - p)])
            calibrated[:, c] = self.models[c].predict_proba(X_trans)[:, 1]
        
        # Re-normalize to sum to 1
        row_sums = calibrated.sum(axis=1, keepdims=True)
        row_sums = np.where(row_sums == 1, 1, row_sums)
        return calibrated / row_sums


class TemperatureScaler:
    """Temperature Scaling (NLL minimization via single scalar temperature T)."""
    def __init__(self):
        self.temp = 1.0

    def fit(self, probs: np.ndarray, y: np.ndarray):
        # Clip to prevent log(0)
        p = np.clip(probs, 1e-15, 1 - 1e-15)
        # Reconstruct logits (log-odds)
        logits = np.log(p)

        def objective(t):
            # Compute temperature-scaled softmax
            scaled_logits = logits / t
            exp_logits = np.exp(scaled_logits - np.max(scaled_logits, axis=1, keepdims=True))
            softmax_probs = exp_logits / np.sum(exp_logits, axis=1, keepdims=True)
            # Clip
            softmax_probs = np.clip(softmax_probs, 1e-15, 1 - 1e-15)
            # Calculate negative log-likelihood (log loss)
            loss = 0.0
            for i, val in enumerate(y):
                loss -= np.log(softmax_probs[i, val])
            return loss / len(y)

        # Optimize T > 0
        res = minimize_scalar(objective, bounds=(0.1, 10.0), method='bounded')
        self.temp = float(res.x)
        return self

    def predict(self, probs: np.ndarray) -> np.ndarray:
        p = np.clip(probs, 1e-15, 1 - 1e-15)
        logits = np.log(p)
        scaled_logits = logits / self.temp
        exp_logits = np.exp(scaled_logits - np.max(scaled_logits, axis=1, keepdims=True))
        return exp_logits / np.sum(exp_logits, axis=1, keepdims=True)
