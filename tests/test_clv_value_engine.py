"""Unit and Integration Tests for Phase 6: CLV & Value Betting Engine."""

import pytest
import numpy as np
from unittest.mock import MagicMock

from src.model.value_clv_engine import (
    clean_implied_probabilities,
    calculate_edge,
    classify_value,
    calculate_clv_pct,
    classify_clv,
    calculate_edge_movement
)
from src.evaluator.market_builder import BetSelector, MarketBuilder


def test_implied_probability_cleaning():
    """Verify that bookmaker overround margin is cleaned proportionally and sums to 1.0."""
    # Scenario 1: MS1 = 2.05, MSX = 3.20, MS2 = 3.80
    # Overround margin sum = 1/2.05 + 1/3.20 + 1/3.80 = 0.4878 + 0.3125 + 0.2632 = 1.0635
    h_prob, d_prob, a_prob = clean_implied_probabilities(2.05, 3.20, 3.80)
    
    assert abs((h_prob + d_prob + a_prob) - 1.0) < 1e-6
    assert h_prob > 0
    assert d_prob > 0
    assert a_prob > 0
    
    # Margin-free probs should be lower than raw implied
    assert h_prob < (1.0 / 2.05)
    assert d_prob < (1.0 / 3.20)
    assert a_prob < (1.0 / 3.80)

    # Scenario 2: Invalid odds inputs should return uniform fallback
    fallback_h, fallback_d, fallback_a = clean_implied_probabilities(0.0, 1.0, -1.5)
    assert abs((fallback_h + fallback_d + fallback_a) - 1.0) < 1e-6
    assert fallback_h == 0.333
    assert fallback_d == 0.333


def test_value_edge_classification():
    """Verify edge classification bounds."""
    # NO_VALUE: edge < 2%
    assert classify_value(0.015) == "NO_VALUE"
    assert classify_value(-0.05) == "NO_VALUE"
    
    # LOW_VALUE: 2% <= edge < 5%
    assert classify_value(0.02) == "LOW_VALUE"
    assert classify_value(0.049) == "LOW_VALUE"
    
    # MEDIUM_VALUE: 5% <= edge < 8%
    assert classify_value(0.05) == "MEDIUM_VALUE"
    assert classify_value(0.079) == "MEDIUM_VALUE"
    
    # HIGH_VALUE: edge >= 8%
    assert classify_value(0.08) == "HIGH_VALUE"
    assert classify_value(0.12) == "HIGH_VALUE"


def test_clv_calculation_and_classification():
    """Verify Closing Line Value percentage changes and group classifications."""
    # CLV % = ((closing - opening) / opening) * 100
    
    # Case 1: STRONG_POSITIVE_CLV (clv >= 10.0%)
    # ((2.2 - 2.0) / 2.0) * 100 = 10.0%
    clv1 = calculate_clv_pct(2.0, 2.2)
    assert clv1 == 10.0
    assert classify_clv(clv1) == "STRONG_POSITIVE_CLV"
    
    # Case 2: POSITIVE_CLV (2.0% <= clv < 10.0%)
    # ((2.1 - 2.0) / 2.0) * 100 = 5.0%
    clv2 = calculate_clv_pct(2.0, 2.1)
    assert clv2 == 5.0
    assert classify_clv(clv2) == "POSITIVE_CLV"
    
    # Case 3: NEUTRAL_CLV (-2.0% < clv < 2.0%)
    clv3 = calculate_clv_pct(2.0, 2.01)
    assert abs(clv3 - 0.5) < 1e-6
    assert classify_clv(clv3) == "NEUTRAL_CLV"
    
    # Case 4: NEGATIVE_CLV (-10.0% < clv <= -2.0%)
    clv4 = calculate_clv_pct(2.0, 1.9)
    assert clv4 == -5.0
    assert classify_clv(clv4) == "NEGATIVE_CLV"
    
    # Case 5: STRONG_NEGATIVE_CLV (clv <= -10.0%)
    clv5 = calculate_clv_pct(2.0, 1.7)
    assert clv5 == -15.0
    assert classify_clv(clv5) == "STRONG_NEGATIVE_CLV"

    # Edge movement check
    # raw check: 1/close - 1/open
    movement = calculate_edge_movement(2.0, 2.2, 0.60)
    assert movement == round((1.0 / 2.2) - (1.0 / 2.0), 4)


def test_4_factor_decision_score():
    """Verify that 4-factor Decision Score blending reacts appropriately to parameters."""
    selector = BetSelector()
    
    # Base score computation
    base_ds = selector.calculate_decision_score(
        prob=0.60, confidence=8.0, sample_size=100, coverage=0.20,
        edge=0.0, avg_clv=0.0, is_derby=False, power_loss_pct=0.0, model_agreement=1.0
    )
    
    # Adding edge (ValueScore = 1.2 * Edge)
    ds_with_edge = selector.calculate_decision_score(
        prob=0.60, confidence=8.0, sample_size=100, coverage=0.20,
        edge=0.05, avg_clv=0.0, is_derby=False, power_loss_pct=0.0, model_agreement=1.0
    )
    assert ds_with_edge > base_ds
    assert abs(ds_with_edge - base_ds - 1.2 * 0.05) < 1e-6

    # Adding average CLV (CLVHistoryScore = 0.5 * (AvgCLV / 100))
    ds_with_clv = selector.calculate_decision_score(
        prob=0.60, confidence=8.0, sample_size=100, coverage=0.20,
        edge=0.0, avg_clv=5.0, is_derby=False, power_loss_pct=0.0, model_agreement=1.0
    )
    assert ds_with_clv > base_ds
    assert abs(ds_with_clv - base_ds - 0.5 * (5.0 / 100.0)) < 1e-6

    # Risk penalties (Derby = -0.05, power loss, model disagreement)
    ds_derby = selector.calculate_decision_score(
        prob=0.60, confidence=8.0, sample_size=100, coverage=0.20,
        edge=0.0, avg_clv=0.0, is_derby=True, power_loss_pct=0.0, model_agreement=1.0
    )
    assert ds_derby < base_ds
    assert abs(ds_derby - base_ds + 0.05) < 1e-6


def test_double_gate_validation():
    """Verify that BetSelector respects the double-gate filter: edge >= 2% AND probability >= threshold."""
    selector = BetSelector()
    builder = MarketBuilder()
    
    # 1. High probability, high edge (should be a PLAY)
    # Model probability: Home=0.70, Draw=0.15, Away=0.15
    # Market odds: Home=2.00, Draw=3.40, Away=4.00 (implied Home ~ 47%) -> Edge ~ 23%
    markets = builder.build_markets(h_prob=0.70, d_prob=0.15, a_prob=0.15, base_confidence=8.0)
    odds = {"home_odds": 2.00, "draw_odds": 3.40, "away_odds": 4.00}
    
    bet = selector.select_best_bet(markets, league_code="E0", db=None, odds=odds)
    assert bet["decision"] == "PLAY"
    assert bet["market"] == "1"
    assert bet["edge"] >= 0.02
    assert bet["probability"] >= bet["threshold"]

    # 2. High probability, but negative edge (should be a SKIP)
    # Model Home prob = 0.70
    # Market odds: Home=1.20 (implied Home ~ 80%+) -> negative edge
    markets_neg = builder.build_markets(h_prob=0.70, d_prob=0.15, a_prob=0.15, base_confidence=8.0)
    odds_neg = {"home_odds": 1.20, "draw_odds": 6.00, "away_odds": 12.00}
    
    bet_neg = selector.select_best_bet(markets_neg, league_code="E0", db=None, odds=odds_neg)
    assert bet_neg["decision"] == "SKIP"
    assert bet_neg["edge"] < 0.02
