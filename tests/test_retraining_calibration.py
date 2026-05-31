"""Automated test suite for Retraining, Calibration & Explainability (Phase X Verification)."""

import pytest
import numpy as np
from pathlib import Path

from src.model.calibration_lab import PlattCalibrator, IsotonicCalibrator, BetaCalibrator, TemperatureScaler
from src.model.shap_explainer import SHAPExplainer
from src.model.ensemble import StackingEnsemble
from config.constants import FEATURE_COLUMNS

def test_calibration_lab_estimators():
    # Generate mock probabilities and binary outcomes
    np.random.seed(42)
    n_samples = 100
    
    # Mock raw probabilities (uncalibrated)
    raw_probs = np.random.uniform(0.1, 0.9, size=(n_samples, 3))
    # Normalize
    raw_probs /= raw_probs.sum(axis=1, keepdims=True)
    
    # Mock labels (0, 1, 2)
    y = np.random.choice([0, 1, 2], size=n_samples)
    
    # Test Platt Calibrator
    platt = PlattCalibrator()
    platt.fit(raw_probs, y)
    cal_platt = platt.predict(raw_probs)
    assert cal_platt.shape == (n_samples, 3)
    assert np.allclose(cal_platt.sum(axis=1), 1.0)
    
    # Test Isotonic Calibrator
    iso = IsotonicCalibrator()
    iso.fit(raw_probs, y)
    cal_iso = iso.predict(raw_probs)
    assert cal_iso.shape == (n_samples, 3)
    assert np.allclose(cal_iso.sum(axis=1), 1.0)
    
    # Test Beta Calibrator
    beta = BetaCalibrator()
    beta.fit(raw_probs, y)
    cal_beta = beta.predict(raw_probs)
    assert cal_beta.shape == (n_samples, 3)
    assert np.allclose(cal_beta.sum(axis=1), 1.0)
    
    # Test Temperature Scaler
    temp = TemperatureScaler()
    temp.fit(raw_probs, y)
    cal_temp = temp.predict(raw_probs)
    assert cal_temp.shape == (n_samples, 3)
    assert np.allclose(cal_temp.sum(axis=1), 1.0)
    assert temp.temp > 0.0

def test_shap_explainer():
    explainer = SHAPExplainer()
    # If the XGBoost model is fitted, test explaining a single vector
    if explainer.explainer is not None:
        X = np.random.uniform(0.0, 1.0, size=(1, len(FEATURE_COLUMNS)))
        factors = explainer.explain_match(X, predicted_class_idx=0)
        assert isinstance(factors, list)
        if len(factors) > 0:
            assert "direction" in factors[0]
            assert "text" in factors[0]
            assert "impact" in factors[0]
            assert factors[0]["direction"] in ["+", "-"]
    else:
        # If no model is saved yet (e.g. testing in a fresh setup), it should return empty list gracefully
        X = np.random.uniform(0.0, 1.0, size=(1, len(FEATURE_COLUMNS)))
        factors = explainer.explain_match(X, predicted_class_idx=0)
        assert factors == []

def test_stacking_ensemble_loading():
    ensemble = StackingEnsemble()
    # Verify ensemble can be loaded from default models directory
    from config.settings import MODELS_DIR
    ensemble_path = MODELS_DIR / "ensemble"
    if (ensemble_path / "meta.pkl").exists():
        ensemble.load(ensemble_path)
        assert ensemble._trained
        assert ensemble.xgb_model is not None
        assert ensemble.lgb_model is not None
        assert ensemble.cat_model is not None
        assert ensemble.meta_learner is not None
