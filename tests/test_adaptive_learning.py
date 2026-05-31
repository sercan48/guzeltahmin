"""Unit and Integration Tests for Phase 7: CLV Feedback Loop & Adaptive Learning Engine."""

import os
import json
import pytest
import numpy as np
from unittest.mock import MagicMock, patch
from pathlib import Path
from fastapi.testclient import TestClient

from src.model.adaptive_learning import AdaptiveLearningEngine
from src.evaluator.market_builder import BetSelector, MarketBuilder
from app.api import app

# Test temp config paths
TEST_ROOT = Path(__file__).parent.parent
TEST_WEIGHTS_PATH = TEST_ROOT / "data" / "dynamic_feature_weights.json"
TEST_BIAS_PATH = TEST_ROOT / "data" / "market_bias_scores.json"
TEST_THRESHOLD_STATE_PATH = TEST_ROOT / "data" / "league_threshold_state.json"


@pytest.fixture(autouse=True)
def clean_test_files():
    """Ensure clean json files for testing, removing them after test run."""
    # Setup
    for p in [TEST_WEIGHTS_PATH, TEST_BIAS_PATH, TEST_THRESHOLD_STATE_PATH]:
        if p.exists():
            try:
                p.unlink()
            except Exception:
                pass
    yield
    # Teardown
    for p in [TEST_WEIGHTS_PATH, TEST_BIAS_PATH, TEST_THRESHOLD_STATE_PATH]:
        if p.exists():
            try:
                p.unlink()
            except Exception:
                pass


def test_weights_initialization():
    """Verify that JSON config files are initialized with correct bounds and defaults."""
    db = MagicMock()
    engine = AdaptiveLearningEngine(db)
    
    assert TEST_WEIGHTS_PATH.exists()
    assert TEST_BIAS_PATH.exists()
    assert TEST_THRESHOLD_STATE_PATH.exists()

    weights = engine._load_json(TEST_WEIGHTS_PATH)
    assert weights["home_team_strength"] == 0.0
    assert weights["travel_distance_km"] == 0.0

    biases = engine._load_json(TEST_BIAS_PATH)
    assert biases["DNB1"] == 0.5
    assert biases["1X"] == 0.5


def test_market_bias_learning():
    """Verify that positive CLV increments market bias, and negative CLV decrements it, respecting [0, 1] bounds."""
    db = MagicMock()
    engine = AdaptiveLearningEngine(db)

    # 1. Positive CLV should increment selection bias
    engine._update_market_bias("1X", 4.5)
    bias = engine._load_json(TEST_BIAS_PATH)
    assert bias["1X"] == 0.51

    # 2. Negative CLV should decrement selection bias
    engine._update_market_bias("1X", -2.5)
    bias = engine._load_json(TEST_BIAS_PATH)
    assert bias["1X"] == 0.50

    # 3. Test upper bound (1.0)
    for _ in range(60):
        engine._update_market_bias("1", 3.0)
    bias = engine._load_json(TEST_BIAS_PATH)
    assert bias["1"] == 1.0

    # 4. Test lower bound (0.0)
    for _ in range(60):
        engine._update_market_bias("DNB2", -3.0)
    bias = engine._load_json(TEST_BIAS_PATH)
    assert bias["DNB2"] == 0.0


def test_league_adaptive_thresholds():
    """Verify that league outcome thresholds adapt correctly based on rolling CLV, clamped to [0.55, 0.80]."""
    db = MagicMock()
    
    # Mock DB fetchone to return negative rolling CLV
    db.fetchone.return_value = {"avg_clv": -1.5}
    engine = AdaptiveLearningEngine(db)
    
    # 1. Negative CLV should increment thresholds
    engine._update_league_thresholds("E0")
    state = engine._load_json(TEST_THRESHOLD_STATE_PATH)
    assert state["E0"]["1"] == 0.63 # base E0 "1" = 0.62 + 0.01 = 0.63

    # 2. Positive CLV (> 5%) should decrement thresholds
    db.fetchone.return_value = {"avg_clv": 6.2}
    engine._update_league_thresholds("E0")
    state = engine._load_json(TEST_THRESHOLD_STATE_PATH)
    assert state["E0"]["1"] == 0.625 # 0.63 - 0.005 = 0.625

    # 3. Verify upper bound constraint (0.80)
    db.fetchone.return_value = {"avg_clv": -2.0}
    for _ in range(25):
        engine._update_league_thresholds("E0")
    state = engine._load_json(TEST_THRESHOLD_STATE_PATH)
    assert state["E0"]["1"] == 0.80

    # 4. Verify lower bound constraint (0.55)
    db.fetchone.return_value = {"avg_clv": 8.0}
    for _ in range(60):
        engine._update_league_thresholds("E0")
    state = engine._load_json(TEST_THRESHOLD_STATE_PATH)
    assert state["E0"]["1"] == 0.55


def test_drift_detection_levels():
    """Verify drift detection alert classification (Level 1 and Level 3 warnings)."""
    db = MagicMock()
    
    # Level 1: 7-day average CLV < 0
    db.fetchone.return_value = {"avg_clv": -0.8}
    db.fetchall.return_value = [] # no edge list variance change
    engine = AdaptiveLearningEngine(db)
    
    drift = engine.detect_drift()
    assert drift["level"] == 1
    assert "WARNING" in drift["message"]

    # Level 3: Severe CLV drop < -5.0
    db.fetchone.return_value = {"avg_clv": -5.6}
    drift = engine.detect_drift()
    assert drift["level"] == 3
    assert "CRITICAL" in drift["message"]


def test_decision_score_adaptation_blending():
    """Verify that CLV feedback scores and market bias adjustments are correctly blended in the Decision Score."""
    selector = BetSelector()
    
    # Configure custom feature weights
    weights = {"home_team_strength": 0.8, "travel_distance_km": -0.5}
    selector.feature_weights = weights
    
    # Configure custom bias scores
    bias = {"1": 0.8, "DNB1": 0.3}
    selector.market_bias_scores = bias

    # 1. Base check without weights
    score_base = selector.calculate_decision_score(
        prob=0.60, confidence=8.0, sample_size=100, coverage=0.20,
        clv_feedback_score=0.0, market_bias_adjustment=0.0
    )

    # 2. Check value blending
    # Market bias adjustment for "1" = 0.1 * (0.8 - 0.5) = 0.03
    bias_adj = 0.1 * (0.8 - 0.5)
    score_adjusted = selector.calculate_decision_score(
        prob=0.60, confidence=8.0, sample_size=100, coverage=0.20,
        clv_feedback_score=0.05, market_bias_adjustment=bias_adj
    )
    
    assert score_adjusted > score_base
    assert abs(score_adjusted - score_base - 0.05 - 0.03) < 1e-6


def test_fastapi_drift_and_weights_endpoints():
    """Verify that the FastAPI drift and weights endpoints return valid schema answers."""
    client = TestClient(app)
    
    # Initialize files
    AdaptiveLearningEngine(None)
    
    # Weights Endpoint Check
    response_w = client.get("/api/analytics/weights")
    assert response_w.status_code == 200
    data_w = response_w.json()
    assert "feature_weights" in data_w
    assert "market_biases" in data_w
    assert data_w["market_biases"]["DNB1"] == 0.5

    # Drift Endpoint Check (with mocked DB state)
    with patch("app.api.get_backend") as mock_get_backend:
        mock_db = MagicMock()
        mock_db.fetchone.return_value = {"avg_clv": 2.4}
        mock_db.fetchall.return_value = [{"value_edge": 0.04}]
        mock_get_backend.return_value = mock_db
        
        response_d = client.get("/api/analytics/drift")
        assert response_d.status_code == 200
        data_d = response_d.json()
        assert "level" in data_d
        assert data_d["level"] == 0
