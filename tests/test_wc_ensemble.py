"""
Tests for Ensemble Architecture blending and contradiction logic using AAA pattern.
"""
import pytest
import sys
from pathlib import Path

# Add project root to path
sys.path.append(str(Path(__file__).parent.parent))

from src.model.wc_ensemble_inference import blend_models, calculate_ensemble_confidence, get_top_pick

# --- AAA Pattern Unit Tests ---

def test_get_top_pick():
    # Arrange
    probs_away = [60.0, 30.0, 10.0]
    probs_draw = [10.0, 50.0, 40.0]
    probs_home = [20.0, 20.0, 60.0]
    
    # Act & Assert
    assert get_top_pick(probs_away) == "AWAY"
    assert get_top_pick(probs_draw) == "DRAW"
    assert get_top_pick(probs_home) == "HOME"

def test_blend_models_weighting():
    # Arrange
    mc_probs = [20.0, 30.0, 50.0]
    xgb_probs = [40.0, 20.0, 40.0]
    mc_conf = 60.0 # 60% confidence -> mc_weight = 0.6, xgb_weight = 0.4
    
    # Act
    blended, mc_wt, xgb_wt = blend_models(mc_probs, xgb_probs, mc_conf)
    
    # Assert
    assert mc_wt == 0.6
    assert xgb_wt == 0.4
    # Away = 20*0.6 + 40*0.4 = 12 + 16 = 28
    assert round(blended[0], 2) == 28.0
    # Draw = 30*0.6 + 20*0.4 = 18 + 8 = 26
    assert round(blended[1], 2) == 26.0
    # Home = 50*0.6 + 40*0.4 = 30 + 16 = 46
    assert round(blended[2], 2) == 46.0

def test_blend_models_bounds():
    # Arrange
    mc_probs = [33.3, 33.3, 33.4]
    xgb_probs = [33.3, 33.3, 33.4]
    # Extreme low and high confidences to test 30% floor and 70% ceiling
    low_conf = 10.0
    high_conf = 95.0
    
    # Act
    _, mc_wt_low, _ = blend_models(mc_probs, xgb_probs, low_conf)
    _, mc_wt_high, _ = blend_models(mc_probs, xgb_probs, high_conf)
    
    # Assert
    assert mc_wt_low == 0.3 # Floor
    assert mc_wt_high == 0.7 # Ceiling

def test_ensemble_confidence_strong_consensus():
    # Arrange
    mc_probs = [10.0, 20.0, 70.0] # Home > 50
    xgb_probs = [20.0, 20.0, 60.0] # Home > 50
    base_conf = 80.0
    
    # Act
    final_conf, status = calculate_ensemble_confidence(mc_probs, xgb_probs, base_conf)
    
    # Assert
    assert status == "🎯 STRONG CONSENSUS"
    assert final_conf == 96.0 # 80 * 1.2

def test_ensemble_confidence_soft_consensus():
    # Arrange
    mc_probs = [10.0, 45.0, 45.0] # Home is 45 (max) but not > 50
    xgb_probs = [30.0, 30.0, 40.0] # Home is 40 (max) but not > 50
    # Wait, in the code get_top_pick returns first max.
    # Actually if mc_probs=[10, 45, 45], index(max) finds 45 at index 1 -> DRAW.
    # Let's adjust slightly.
    mc_probs = [10.0, 40.0, 50.0]
    xgb_probs = [30.0, 30.0, 40.0]
    base_conf = 80.0
    
    # Act
    final_conf, status = calculate_ensemble_confidence(mc_probs, xgb_probs, base_conf)
    
    # Assert
    assert status == "🤝 MODELS AGREE"
    assert final_conf == 84.0 # 80 * 1.05

def test_ensemble_confidence_high_contradiction():
    # Arrange
    mc_probs = [20.0, 20.0, 60.0] # HOME (max 60)
    xgb_probs = [70.0, 20.0, 10.0] # AWAY (max 70)
    # Divergence = |60 - 70| = 10, Wait. 
    # Actually if divergence > 20 is high contradiction. Let's make it 90 and 60.
    mc_probs = [20.0, 20.0, 60.0] # HOME (max 60)
    xgb_probs = [90.0, 10.0, 0.0] # AWAY (max 90)
    base_conf = 80.0
    
    # Act
    final_conf, status = calculate_ensemble_confidence(mc_probs, xgb_probs, base_conf)
    
    # Assert
    assert status == "⚠️ HIGH CONTRADICTION (Risk of Upset)"
    assert final_conf == 32.0 # 80 * 0.40

def test_ensemble_confidence_low_contradiction():
    # Arrange
    mc_probs = [20.0, 20.0, 60.0] # HOME (max 60)
    xgb_probs = [50.0, 30.0, 20.0] # AWAY (max 50)
    # Divergence = |60 - 50| = 10, which is <= 20
    base_conf = 80.0
    
    # Act
    final_conf, status = calculate_ensemble_confidence(mc_probs, xgb_probs, base_conf)
    
    # Assert
    assert status == "⚖️ DIVERGING PREDICTIONS"
    assert final_conf == 60.0 # 80 * 0.75
