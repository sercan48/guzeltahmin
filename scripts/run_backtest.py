"""Run full backtest on historical data."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import argparse
import pandas as pd

from config.settings import PROCESSED_DIR
from src.model.trainer import full_training_pipeline, load_model
from src.evaluator.backtester import backtest_season, backtest_report, print_backtest_report
from src.evaluator.confusion_analyzer import analyze_errors, print_error_analysis, calibration_analysis
from src.evaluator.value_hunter import find_value_bets
from src.evaluator.bankometer import bankometer, banko_summary


def run_backtest(season: str = None, leagues: str = "all"):
    """Full backtest pipeline: train → predict → analyze."""
    print("=" * 60)
    print("  Güzel Tahmin — Full Backtest")
    print("=" * 60)

    # Load features
    features_path = PROCESSED_DIR / "features.csv"
    if not features_path.exists():
        print("[ERROR] features.csv not found. Run scripts/build_features.py first!")
        return

    df = pd.read_csv(features_path)
    print(f"[DATA] Loaded {len(df)} feature rows")

    if season:
        df = df[df["season"] == season]
        print(f"[FILTER] Season={season}: {len(df)} matches")

    if leagues != "all":
        league_list = leagues.split(",")
        df = df[df["league_code"].isin(league_list)]
        print(f"[FILTER] Leagues={leagues}: {len(df)} matches")

    # Train
    print("\n--- TRAINING ---")
    model, accuracy, report = full_training_pipeline(df)

    # Backtest
    print("\n--- BACKTEST ---")
    results = backtest_season(df, model)
    bt_report = backtest_report(results)
    print_backtest_report(bt_report)

    # Error analysis
    print("\n--- ERROR ANALYSIS ---")
    errors = analyze_errors(results)
    print_error_analysis(errors)

    # Calibration
    cal = calibration_analysis(results)
    print("\n--- CALIBRATION ---")
    for outcome, bins in cal.items():
        print(f"\n  {outcome}:")
        for b in bins:
            gap_indicator = "OK" if b["gap"] < 0.1 else "WARN" if b["gap"] < 0.2 else "BAD"
            print(f"    {b['bin']}: pred={b['predicted']:.2f} actual={b['actual']:.2f} "
                  f"gap={b['gap']:.2f} {gap_indicator} (n={b['count']})")

    # Value bet analysis
    if "home_odds" in results.columns:
        print("\n--- VALUE BET ANALYSIS ---")
        predictions = results.to_dict("records")
        for p in predictions:
            p["confidence_score"] = 70  # Default for backtest
        vb = find_value_bets(predictions)
        print(f"  Value bets found: {len(vb)}")

        bankos = bankometer(predictions)
        print(banko_summary(bankos))

    print("\n[DONE] Backtest complete!")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run backtest")
    parser.add_argument("--season", type=str, default=None, help="Season filter e.g. '2024-2025'")
    parser.add_argument("--leagues", type=str, default="all", help="Comma-separated league codes")
    args = parser.parse_args()

    run_backtest(args.season, args.leagues)
