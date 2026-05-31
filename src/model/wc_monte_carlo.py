"""
World Cup Monte Carlo Simulation Engine.
Adjusts base xG using advanced features and simulates 10,000 matches.
"""
import numpy as np

class TeamStats:
    def __init__(self, elo, att_vs_def_delta, synergy, fatigue):
        self.elo = elo
        self.att_vs_def_delta = att_vs_def_delta
        self.synergy = synergy
        self.fatigue = fatigue

def calculate_base_xg(elo_a, elo_b):
    """Simple baseline xG derived from Elo difference."""
    diff = elo_a - elo_b
    # Base expected goals around 1.3 for equal teams
    return max(0.1, 1.3 + (diff * 0.002))

def run_monte_carlo_simulation(team_a_stats: TeamStats, team_b_stats: TeamStats, simulations=10000):
    """
    Runs a Monte Carlo simulation based on adjusted Poisson lambdas.
    Returns Probabilities, xG, and a Variance-based Confidence Score.
    """
    # 1. Base Expectancy from Elo
    base_lambda_a = calculate_base_xg(team_a_stats.elo, team_b_stats.elo)
    base_lambda_b = calculate_base_xg(team_b_stats.elo, team_a_stats.elo)
    
    # 2. Adjust Lambda using Advanced Features
    # 5% boost per point of att_vs_def delta
    adj_lambda_a = base_lambda_a * (1 + (team_a_stats.att_vs_def_delta * 0.05))
    adj_lambda_a += (team_a_stats.synergy * 0.01)
    adj_lambda_a -= (team_a_stats.fatigue * 0.02)
    adj_lambda_a = max(0.1, adj_lambda_a) # Floor xG
    
    adj_lambda_b = base_lambda_b * (1 + (team_b_stats.att_vs_def_delta * 0.05))
    adj_lambda_b += (team_b_stats.synergy * 0.01)
    adj_lambda_b -= (team_b_stats.fatigue * 0.02)
    adj_lambda_b = max(0.1, adj_lambda_b)

    # 3. Simulate Matches
    # Ensure reproducibility for testing
    sim_a = np.random.poisson(lam=adj_lambda_a, size=simulations)
    sim_b = np.random.poisson(lam=adj_lambda_b, size=simulations)
    
    # 4. Aggregate Results
    home_wins = np.sum(sim_a > sim_b)
    draws = np.sum(sim_a == sim_b)
    away_wins = np.sum(sim_a < sim_b)
    
    prob_h = (home_wins / simulations) * 100
    prob_d = (draws / simulations) * 100
    prob_a = (away_wins / simulations) * 100
    
    # 5. Calculate Confidence Score
    # Inversely proportional to the variance of the goal difference
    goal_diffs = sim_a - sim_b
    variance = np.var(goal_diffs)
    # Lower variance = highly predictable = higher confidence
    confidence = max(10, min(100, 100 - (variance * 15))) 
    
    return {
        "home_win_prob": round(prob_h, 2),
        "draw_prob": round(prob_d, 2),
        "away_win_prob": round(prob_a, 2),
        "expected_goals_a": round(adj_lambda_a, 2),
        "expected_goals_b": round(adj_lambda_b, 2),
        "confidence_score": round(confidence, 2)
    }
