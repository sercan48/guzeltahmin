"""Unit and Integration Tests for Phase 8: Adaptive Threshold Optimization Engine."""

import os
import json
import pytest
from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient

from src.db.base import get_backend
from src.model.adaptive_thresholds import AdaptiveThresholdOptimizer
from src.evaluator.market_builder import BetSelector
from app.api import app

@pytest.fixture
def mock_db():
    db = MagicMock()
    return db

def test_threshold_state_database_versioning(mock_db):
    """Verify that save_threshold_state increments version and manages active flag correctly."""
    optimizer = AdaptiveThresholdOptimizer(mock_db)
    
    # Mock COALESCE MAX version to return 2
    mock_db.fetchone.return_value = {"max_v": 2}
    
    thresholds = {"1": 0.62, "X": 0.32, "2": 0.60}
    new_version = optimizer.save_threshold_state("E0", thresholds, 15.2, 2.1, 0.25)
    
    assert new_version == 3
    # Check that previous versions are deactivated
    mock_db.execute.assert_any_call("""
                UPDATE threshold_state 
                SET is_active = 0 
                WHERE league_id = ?
            """, ("E0",))
    
    # Check that new rows are inserted with active = 1
    mock_db.execute.assert_any_call("""
                    INSERT INTO threshold_state (
                        league_id, market_type, threshold_value, roi_30d, clv_30d, coverage_30d, version, is_active
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, 1)
                """, ("E0", "1", 0.62, 15.2, 2.1, 0.25, 3))


def test_market_aware_threshold_multipliers():
    """Verify market multipliers are correctly applied to fallback baseline configurations."""
    selector = BetSelector()
    
    # Clear local files state to force defaults fallback
    selector.thresholds = {}
    selector.thresholds_state = {}
    
    # Europe Stable outcome '1' (base = 0.62). Multiplier = 1.05 -> 0.62 * 1.05 = 0.651
    val_ms = selector.get_threshold(league_type="EUROPE_STABLE", outcome="1", league_code="DUMMY_LEAGUE", db=None)
    assert val_ms == 0.651
    
    # Europe Stable outcome '1X' (DC, base = 0.75). Multiplier = 0.98 -> 0.75 * 0.98 = 0.735
    val_dc = selector.get_threshold(league_type="EUROPE_STABLE", outcome="1X", league_code="DUMMY_LEAGUE", db=None)
    assert val_dc == 0.735

    # Europe Stable outcome 'DNB1' (DNB, base = 0.65). Multiplier = 0.92 -> 0.65 * 0.92 = 0.598
    val_dnb = selector.get_threshold(league_type="EUROPE_STABLE", outcome="DNB1", league_code="DUMMY_LEAGUE", db=None)
    assert val_dnb == 0.598


def test_safety_rollback_trigger(mock_db):
    """Verify that a drop in 7-day rolling ROI triggers a rollback to the previous best version."""
    optimizer = AdaptiveThresholdOptimizer(mock_db)
    
    # Mock 7-day predictions for league 'E0' with 3 losing bets (ROI = -100%)
    mock_db.fetchall.return_value = [
        {"predicted_result": "1", "actual_result": "A", "prediction_odds": 2.0, "ft_result": "A"},
        {"predicted_result": "1", "actual_result": "A", "prediction_odds": 2.0, "ft_result": "A"},
        {"predicted_result": "2", "actual_result": "H", "prediction_odds": 2.0, "ft_result": "H"}
    ]
    
    # Mock current active version to be v5
    mock_db.fetchone.side_effect = [
        {"version": 5}, # active version check
        {"version": 3}  # previous best version check (excluding 5)
    ]
    
    res = optimizer.check_and_rollback_league("E0")
    
    assert res["status"] == "rolled_back"
    assert res["reverted_from"] == 5
    assert res["reverted_to"] == 3
    
    # Check SQL update queries de-activating v5 and activating v3
    mock_db.execute.assert_any_call("""
                        UPDATE threshold_state 
                        SET is_active = 0 
                        WHERE league_id = ? AND version = ?
                    """, ("E0", 5))
    
    mock_db.execute.assert_any_call("""
                            UPDATE threshold_state 
                            SET is_active = 1 
                            WHERE league_id = ? AND version = ?
                        """, ("E0", 3))


def test_daily_micro_adjustments(mock_db):
    """Verify that daily adjustment adapts thresholds dynamically based on ROI and CLV trends."""
    optimizer = AdaptiveThresholdOptimizer(mock_db)
    
    # 1. Test CLV Improvement -> threshold decreases by 0.002
    mock_db.fetchone.side_effect = [
        {"avg_clv": 3.5}, # 7-day avg clv
        {"avg_clv": 1.2}  # 30-day baseline avg clv (improved by > 1.0%)
    ]
    
    # Mock active thresholds
    mock_db.fetchall.side_effect = [
        [{"market_type": "1", "threshold_value": 0.65}], # get_active_thresholds
        [] # mock empty recent predictions to skip ROI check
    ]
    
    res = optimizer.run_daily_micro_adjustments("E0")
    assert res["status"] == "adjusted"
    assert res["delta"] == -0.002
    assert res["adjusted_thresholds"]["1"] == 0.648


def test_fastapi_thresholds_endpoints():
    """Verify that thresholds endpoint returns valid schema and rollback endpoint functions correctly."""
    client = TestClient(app)
    
    with patch("app.api.get_backend") as mock_get_backend:
        mock_db = MagicMock()
        mock_get_backend.return_value = mock_db
        
        # 1. GET /api/analytics/thresholds
        mock_db.fetchall.return_value = [
            {
                "league_id": "E0", "market_type": "1", "threshold_value": 0.63, 
                "roi_30d": 12.4, "clv_30d": 1.5, "coverage_30d": 0.20, 
                "version": 2, "is_active": 1, "last_updated": "2026-05-31"
            }
        ]
        
        response_get = client.get("/api/analytics/thresholds")
        assert response_get.status_code == 200
        data_get = response_get.json()
        assert "E0" in data_get
        assert data_get["E0"][0]["version"] == 2
        assert data_get["E0"][0]["thresholds"]["1"] == 0.63
        
        # 2. POST /api/analytics/thresholds/rollback
        mock_db.fetchone.return_value = {"cnt": 1} # mock requested version exists
        
        response_post = client.post("/api/analytics/thresholds/rollback", json={"league_id": "E0", "version": 2})
        assert response_post.status_code == 200
        data_post = response_post.json()
        assert data_post["status"] == "success"
        
        # Check updates executed
        mock_db.execute.assert_any_call("""
            UPDATE threshold_state 
            SET is_active = 0 
            WHERE league_id = ?
        """, ("E0",))
        mock_db.execute.assert_any_call("""
            UPDATE threshold_state 
            SET is_active = 1 
            WHERE league_id = ? AND version = ?
        """, ("E0", 2))
