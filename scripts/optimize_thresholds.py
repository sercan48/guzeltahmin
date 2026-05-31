"""Threshold Optimization Lab.

Searches for optimal probability decision thresholds per league type (EUROPE_STABLE,
SUMMER_VOLATILE, HIGH_ROTATION) using Optuna to maximize expected value (EV) and
risk-adjusted ROI under coverage constraints.
"""

import sys
import json
import logging
import pickle
import numpy as np
import pandas as pd
from pathlib import Path
import optuna

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from config.settings import MODELS_DIR, RANDOM_SEED
from config.constants import FEATURE_COLUMNS
from src.model.ensemble import StackingEnsemble
from src.evaluator.market_builder import MarketBuilder
from src.model.league_classifier import LeagueClassifier

logger = logging.getLogger(__name__)

# Suppress Optuna logging to clean output unless warnings occur
optuna.logging.set_verbosity(optuna.logging.WARNING)

def clip_threshold(val: float, min_val: float, max_val: float) -> float:
    return max(min_val, min(val, max_val))

def load_calibrator(league_code: str):
    cal_file = MODELS_DIR / "ensemble" / f"calibrator_{league_code}.pkl"
    if cal_file.exists():
        with open(cal_file, "rb") as f:
            return pickle.load(f)
    return None

def simulate_betting_optuna(df_league: pd.DataFrame, probs: np.ndarray, league_type: str,
                             thresholds: dict, sample_size: int, coverage_val: float) -> dict:
    """Simulate bets and return ROI, EV, risk penalty, coverage penalty, and utility score."""
    market_builder = MarketBuilder()
    
    total_stake = 0.0
    total_profit = 0.0
    win_count = 0
    refund_count = 0
    play_count = 0
    ev_sum = 0.0
    
    probs_played = []
    odds_played = []
    markets_played = []
    
    confidence = 7.0
    conf_factor = 0.85 + 0.30 * (confidence / 10.0)
    sample_size_factor = 1.0 - 0.15 * np.exp(-sample_size / 50.0)
    coverage_factor = 0.9 + 0.2 * min(max(coverage_val, 0.0), 1.0)
    ds_multiplier = conf_factor * sample_size_factor * coverage_factor
    
    market_types = {
        "1": "1X2", "X": "1X2", "2": "1X2",
        "1X": "DC", "X2": "DC", "12": "DC",
        "DNB1": "DNB", "DNB2": "DNB"
    }
    
    def get_preference_bonus(l_type: str, m_type: str) -> float:
        if l_type == "EUROPE_STABLE" and m_type == "1X2":
            return 0.05
        elif l_type == "SUMMER_VOLATILE" and m_type in ["DC", "DNB"]:
            return 0.05
        return 0.0
        
    for idx, row in df_league.reset_index(drop=True).iterrows():
        p = probs[idx]
        
        # Build derived markets
        markets = market_builder.build_markets(p[0], p[1], p[2], confidence)
        
        actual_res = row["ft_result"]
        
        # Get odds
        o_h = float(row["home_odds"])
        o_d = float(row["draw_odds"])
        o_a = float(row["away_odds"])
        
        # Approximate odds for DC and DNB outcomes
        odds_map = {
            "1": o_h,
            "X": o_d,
            "2": o_a,
            "1X": 1.0 / ((1.0/o_h) + (1.0/o_d)) if o_h > 0 and o_d > 0 else 1.15,
            "X2": 1.0 / ((1.0/o_a) + (1.0/o_d)) if o_a > 0 and o_d > 0 else 1.15,
            "12": 1.0 / ((1.0/o_h) + (1.0/o_a)) if o_h > 0 and o_a > 0 else 1.15,
            "DNB1": o_h * (1.0 - 1.0/o_d) if o_d > 1.0 else o_h,
            "DNB2": o_a * (1.0 - 1.0/o_d) if o_d > 1.0 else o_a
        }
        
        # Score candidates
        scored_candidates = []
        for outcome, m_info in markets.items():
            prob = m_info["probability"]
            mkt_type = market_types[outcome]
            
            # Decision score
            ds = prob * ds_multiplier
            
            # Preference bonus
            bonus = get_preference_bonus(league_type, mkt_type)
            score = ds + bonus
            
            thresh = thresholds[outcome]
            
            scored_candidates.append({
                "outcome": outcome,
                "score": score,
                "decision_score": ds,
                "threshold": thresh,
                "probability": prob,
                "odds": odds_map.get(outcome, 1.5)
            })
            
        scored_candidates.sort(key=lambda x: x["score"], reverse=True)
        
        # Find best play
        best_play = None
        for cand in scored_candidates:
            if cand["decision_score"] >= cand["threshold"]:
                best_play = cand
                break
                
        if best_play:
            market_played = best_play["outcome"]
            prob_played = best_play["probability"]
            odds_played_val = best_play["odds"]
            
            stake = 1.0
            bet_win = False
            bet_void = False
            
            # Compute EV
            ev = prob_played * odds_played_val - 1.0
            ev_sum += ev
            
            probs_played.append(prob_played)
            odds_played.append(odds_played_val)
            markets_played.append(market_played)
            
            # Resolve actual outcome
            if market_played == "1" and actual_res == "H":
                bet_win = True
            elif market_played == "X" and actual_res == "D":
                bet_win = True
            elif market_played == "2" and actual_res == "A":
                bet_win = True
            elif market_played == "1X" and actual_res in ["H", "D"]:
                bet_win = True
            elif market_played == "X2" and actual_res in ["A", "D"]:
                bet_win = True
            elif market_played == "12" and actual_res in ["H", "A"]:
                bet_win = True
            elif market_played == "DNB1":
                if actual_res == "H":
                    bet_win = True
                elif actual_res == "D":
                    bet_void = True
            elif market_played == "DNB2":
                if actual_res == "A":
                    bet_win = True
                elif actual_res == "D":
                    bet_void = True
                    
            if bet_win:
                total_profit += (stake * odds_played_val - stake)
                total_stake += stake
                win_count += 1
            elif bet_void:
                refund_count += 1
            else:
                total_profit -= stake
                total_stake += stake
                
            play_count += 1
            
    # Calculate stats
    n_total = len(df_league)
    coverage = play_count / n_total if n_total > 0 else 0.0
    coverage_pct = coverage * 100.0
    roi = (total_profit / total_stake) * 100.0 if total_stake > 0 else 0.0
    avg_ev = ev_sum / play_count if play_count > 0 else 0.0
    
    # Calculate risk metrics
    if play_count > 0:
        prob_var = float(np.var(probs_played))
        odds_std = float(np.std(odds_played))
        draw_dep = sum(1 for m in markets_played if "X" in m or "DNB" in m) / play_count
        risk_penalty = (prob_var * 100.0) + (odds_std * 5.0) + (draw_dep * 10.0)
    else:
        risk_penalty = 0.0
        
    coverage_penalty = max(0.0, 15.0 - coverage_pct)
    
    # Utility score
    ev_scaled = avg_ev * 100.0
    utility = 0.5 * ev_scaled - 0.3 * risk_penalty - 0.2 * coverage_penalty
    
    return {
        "play_count": play_count,
        "coverage_pct": coverage_pct,
        "roi_pct": roi,
        "avg_ev": avg_ev,
        "win_count": win_count,
        "refund_count": refund_count,
        "risk_penalty": risk_penalty,
        "coverage_penalty": coverage_penalty,
        "utility": utility
    }

def optuna_objective(trial, df_league: pd.DataFrame, probs: np.ndarray, league_type: str, sample_size: int, coverage_val: float) -> float:
    # Base thresholds
    base_thresholds = {
        "EUROPE_STABLE": {
            "1": 0.62, "2": 0.60, "X": 0.32,
            "1X": 0.75, "X2": 0.75, "12": 0.75,
            "DNB1": 0.65, "DNB2": 0.65
        },
        "SUMMER_VOLATILE": {
            "1": 0.60, "2": 0.58, "X": 0.33,
            "1X": 0.70, "X2": 0.70, "12": 0.70,
            "DNB1": 0.60, "DNB2": 0.60
        },
        "HIGH_ROTATION": {
            "1": 0.65, "2": 0.62, "X": 0.34,
            "1X": 0.78, "X2": 0.78, "12": 0.78,
            "DNB1": 0.68, "DNB2": 0.68
        }
    }
    
    defaults = base_thresholds.get(league_type, base_thresholds["HIGH_ROTATION"])
    
    # Bounded offsets within [-0.05, 0.05]
    delta_1 = trial.suggest_float("delta_1", -0.05, 0.05)
    delta_2 = trial.suggest_float("delta_2", -0.05, 0.05)
    delta_x = trial.suggest_float("delta_x", -0.05, 0.05)
    delta_1x = trial.suggest_float("delta_1x", -0.05, 0.05)
    delta_x2 = trial.suggest_float("delta_x2", -0.05, 0.05)
    delta_12 = trial.suggest_float("delta_12", -0.05, 0.05)
    delta_dnb1 = trial.suggest_float("delta_dnb1", -0.05, 0.05)
    delta_dnb2 = trial.suggest_float("delta_dnb2", -0.05, 0.05)
    
    # Bounded optimization check for Home/Away (0.55 <= threshold <= 0.75)
    t_1 = clip_threshold(defaults["1"] + delta_1, 0.55, 0.75)
    t_2 = clip_threshold(defaults["2"] + delta_2, 0.55, 0.75)
    t_x = defaults["X"] + delta_x
    t_1x = defaults["1X"] + delta_1x
    t_x2 = defaults["X2"] + delta_x2
    t_12 = defaults["12"] + delta_12
    t_dnb1 = defaults["DNB1"] + delta_dnb1
    t_dnb2 = defaults["DNB2"] + delta_dnb2
    
    thresholds = {
        "1": t_1, "2": t_2, "X": t_x,
        "1X": t_1x, "X2": t_x2, "12": t_12,
        "DNB1": t_dnb1, "DNB2": t_dnb2
    }
    
    stats = simulate_betting_optuna(df_league, probs, league_type, thresholds, sample_size, coverage_val)
    
    # Penalize if play count is too small (e.g. less than 5 plays)
    if stats["play_count"] < 5:
        return stats["utility"] - 50.0
        
    return stats["utility"]

def main():
    print("=" * 60)
    print("  BETTING THRESHOLD OPTIMIZATION LAB (OPTUNA)")
    print("=" * 60)
    
    features_path = Path("data/processed/features.csv")
    if not features_path.exists():
        print(f"Features file not found at {features_path}. Please run build_features.py first.")
        sys.exit(1)
        
    df = pd.read_csv(features_path)
    
    # Enforce missing odds policy (exclude from threshold optimization)
    df = df[df["ft_result"].isin(["H", "D", "A"])].reset_index(drop=True)
    df = df[df["home_odds"].notna() & df["draw_odds"].notna() & df["away_odds"].notna()]
    df = df[(df["home_odds"] > 1.0) & (df["draw_odds"] > 1.0) & (df["away_odds"] > 1.0)].reset_index(drop=True)
    
    ensemble = StackingEnsemble()
    ensemble.load(MODELS_DIR / "ensemble")
    
    available = [c for c in FEATURE_COLUMNS if c in df.columns]
    X_all = df[available].fillna(0).values
    raw_probs = ensemble.predict_proba(X_all)
    
    # Group by Resolved League Type
    classifier = LeagueClassifier()
    df["league_type"] = df["league_code"].apply(classifier.get_league_type)
    
    optimized_thresholds = {}
    
    # Base thresholds
    base_thresholds = {
        "EUROPE_STABLE": {
            "1": 0.62, "2": 0.60, "X": 0.32,
            "1X": 0.75, "X2": 0.75, "12": 0.75,
            "DNB1": 0.65, "DNB2": 0.65
        },
        "SUMMER_VOLATILE": {
            "1": 0.60, "2": 0.58, "X": 0.33,
            "1X": 0.70, "X2": 0.70, "12": 0.70,
            "DNB1": 0.60, "DNB2": 0.60
        },
        "HIGH_ROTATION": {
            "1": 0.65, "2": 0.62, "X": 0.34,
            "1X": 0.78, "X2": 0.78, "12": 0.78,
            "DNB1": 0.68, "DNB2": 0.68
        }
    }
    
    for l_type in ["EUROPE_STABLE", "SUMMER_VOLATILE", "HIGH_ROTATION"]:
        idx = df[df["league_type"] == l_type].index.values
        if len(idx) < 15:
            print(f"Skipping {l_type} due to insufficient matches ({len(idx)}).")
            continue
            
        print(f"\nOptimizing thresholds for {l_type} ({len(idx)} matches with valid odds)...")
        
        df_sub = df.loc[idx].copy()
        raw_sub_probs = raw_probs[idx]
        
        # Apply calibration to subset dynamically
        calibrated_probs = []
        for i, row in df_sub.reset_index(drop=True).iterrows():
            league_code = row["league_code"]
            cal = load_calibrator(league_code)
            raw_prob = raw_sub_probs[i].reshape(1, -1)
            if cal:
                # Avoid re-importing _calibrate_probs by predicting directly
                if hasattr(cal, "predict"):
                    cal_prob = cal.predict(raw_prob)[0]
                elif hasattr(cal, "predict_proba"):
                    cal_prob = cal.predict_proba(raw_prob)[0]
                else:
                    cal_prob = raw_prob[0]
            else:
                cal_prob = raw_prob[0]
            calibrated_probs.append(cal_prob)
            
        calibrated_probs = np.array(calibrated_probs)
        sample_size = len(df_sub)
        coverage_val = 0.20 # baseline play count rate
        
        # Define Optuna Study
        study = optuna.create_study(direction="maximize")
        study.optimize(
            lambda trial: optuna_objective(trial, df_sub, calibrated_probs, l_type, sample_size, coverage_val),
            n_trials=100,
            show_progress_bar=False
        )
        
        # Extract best parameters and rebuild thresholds dictionary
        best_params = study.best_params
        defaults = base_thresholds[l_type]
        
        best_thresholds = {
            "1": float(clip_threshold(defaults["1"] + best_params["delta_1"], 0.55, 0.75)),
            "2": float(clip_threshold(defaults["2"] + best_params["delta_2"], 0.55, 0.75)),
            "X": float(defaults["X"] + best_params["delta_x"]),
            "1X": float(defaults["1X"] + best_params["delta_1x"]),
            "X2": float(defaults["X2"] + best_params["delta_x2"]),
            "12": float(defaults["12"] + best_params["delta_12"]),
            "DNB1": float(defaults["DNB1"] + best_params["delta_dnb1"]),
            "DNB2": float(defaults["DNB2"] + best_params["delta_dnb2"])
        }
        
        final_stats = simulate_betting_optuna(df_sub, calibrated_probs, l_type, best_thresholds, sample_size, coverage_val)
        
        print(f"Optimal Thresholds for {l_type}:")
        for k, v in best_thresholds.items():
            print(f"  Outcome {k}: {v:.3f} (base: {defaults[k]:.3f})")
            
        print(f"Stats: Bets Placed={final_stats['play_count']} | Coverage={final_stats['coverage_pct']:.1f}% | ROI={final_stats['roi_pct']:.2f}% | Avg EV={final_stats['avg_ev']:.4f} | Utility={study.best_value:.4f}")
        
        optimized_thresholds[l_type] = best_thresholds
        
    # Save optimized thresholds to data/optimized_thresholds.json
    out_path = Path("data/optimized_thresholds.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(optimized_thresholds, f, indent=2)
    print(f"\n[OK] Saved optimized thresholds to {out_path}")

if __name__ == "__main__":
    main()
