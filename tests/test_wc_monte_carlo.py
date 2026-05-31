"""
Tests for World Cup Monte Carlo Engine and Advanced Features using AAA pattern.
"""
import pytest
from collections import namedtuple
import sys
from pathlib import Path

# Add project root to path for imports
sys.path.append(str(Path(__file__).parent.parent))

from src.features.wc_advanced_features import (
    Player, avg_rating, calculate_positional_dynamics, 
    calculate_chemistry, calculate_fatigue_and_env
)
from src.model.wc_monte_carlo import TeamStats, calculate_base_xg, run_monte_carlo_simulation

# --- AAA Pattern Unit Tests ---

def test_avg_rating_valid_position():
    # Arrange
    lineup = [
        Player("A", "ATT", "Club1", 10, 80),
        Player("B", "ATT", "Club2", 20, 90)
    ]
    
    # Act
    result = avg_rating(lineup, "ATT")
    
    # Assert
    assert result == 85.0

def test_avg_rating_empty_position():
    # Arrange
    lineup = [Player("A", "ATT", "Club1", 10, 80)]
    
    # Act
    result = avg_rating(lineup, "MID")
    
    # Assert
    assert result == 70.0 # Default fallback

def test_calculate_positional_dynamics():
    # Arrange
    lineup_a = [
        Player("A1", "ATT", "C1", 10, 90),
        Player("A2", "MID", "C1", 10, 80),
        Player("A3", "DEF", "C1", 10, 70),
        Player("A4", "DEF", "C1", 10, 50) # Weak link! Team avg is ~72.5, 50 is < 61.6 (85%)
    ]
    
    lineup_b = [
        Player("B1", "ATT", "C2", 10, 80),
        Player("B2", "MID", "C2", 10, 80),
        Player("B3", "DEF", "C2", 10, 80)
    ]
    
    # Act
    dynamics = calculate_positional_dynamics(lineup_a, lineup_b)
    
    # Assert
    assert dynamics["a_att_vs_b_def"] == 10.0 # (90 - 80)
    assert dynamics["b_att_vs_a_def"] == 20.0 # (80 - 60)
    assert dynamics["a_midfield_dominance"] == 1.0 # (80 / 80)
    assert dynamics["a_weakest_links"] == 1

def test_calculate_chemistry():
    # Arrange
    lineup = [
        Player("A", "ATT", "Bayern", 10, 80),
        Player("B", "MID", "Bayern", 20, 80),
        Player("C", "DEF", "Bayern", 30, 80),
        Player("D", "GK", "Dortmund", 40, 80)
    ]
    
    # Act
    synergy, avg_caps = calculate_chemistry(lineup)
    
    # Assert
    assert synergy == 7.5 # 3 players in Bayern -> 3 * 2.5
    assert avg_caps == 25.0

def test_calculate_fatigue_and_env():
    # Arrange
    distance = 2000
    altitude = 2000
    continent = "Europe"
    
    # Act
    fatigue, env_mod = calculate_fatigue_and_env(distance, altitude, continent)
    
    # Assert
    assert fatigue == 3.0 # (2000/1000) * 1.5
    assert env_mod == -5.0 # Altitude penalty for Europe

def test_calculate_base_xg():
    # Arrange
    elo_a = 1600
    elo_b = 1500
    
    # Act
    xg = calculate_base_xg(elo_a, elo_b)
    
    # Assert
    assert xg == 1.5 # 1.3 + (100 * 0.002) = 1.3 + 0.2

def test_run_monte_carlo_simulation():
    # Arrange
    import numpy as np
    np.random.seed(42) # Deterministic tests
    
    team_a = TeamStats(elo=1700, att_vs_def_delta=5.0, synergy=10.0, fatigue=0.0)
    team_b = TeamStats(elo=1500, att_vs_def_delta=-5.0, synergy=0.0, fatigue=5.0)
    
    # Act
    result = run_monte_carlo_simulation(team_a, team_b, simulations=1000)
    
    # Assert
    assert "home_win_prob" in result
    assert "draw_prob" in result
    assert "away_win_prob" in result
    assert "expected_goals_a" in result
    assert "expected_goals_b" in result
    assert "confidence_score" in result
    
    # Probabilities should sum closely to 100
    total_prob = result["home_win_prob"] + result["draw_prob"] + result["away_win_prob"]
    assert round(total_prob) == 100
    
    # Team A is much stronger, should have higher xG
    assert result["expected_goals_a"] > result["expected_goals_b"]
