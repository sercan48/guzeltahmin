"""Run full 5-season walk-forward backtest across all model types.

Usage:
    python scripts/run_full_backtest.py
    python scripts/run_full_backtest.py --models xgboost,lightgbm
    python scripts/run_full_backtest.py --models all --verbose
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.evaluator.backtester import WalkForwardBacktester, backtest_report, print_backtest_report
from src.db.base import get_backend
from src.model.model_registry import ModelRegistry


def main():
    parser = argparse.ArgumentParser(description="Walk-forward backtest")
    parser.add_argument("--models", default="all",
                        help="Model types: all, xgboost, lightgbm, poisson, ensemble")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    if args.models == "all":
        model_types = ["xgboost", "lightgbm", "poisson", "ensemble"]
    else:
        model_types = [m.strip() for m in args.models.split(",")]

    print("=" * 60)
    print("  WALK-FORWARD BACKTEST — 5 Sezon")
    print("=" * 60)

    bt = WalkForwardBacktester()
    comparison = bt.compare_models(model_types)

    print("\n" + "=" * 60)
    print("  MODEL KARŞILAŞTIRMA")
    print("=" * 60)

    if not comparison.empty:
        for _, row in comparison.iterrows():
            print(f"\n  📊 {row['model_type'].upper()}")
            print(f"     Doğruluk: %{row['avg_accuracy']*100:.1f}")
            print(f"     ROI: %{row['avg_roi']:.1f}")
            print(f"     Brier: {row['avg_brier']:.4f}")
            print(f"     Composite: {row['avg_composite']:.4f}")

        best = comparison.iloc[0]
        print(f"\n  🏆 En İyi Model: {best['model_type'].upper()}")
        print(f"     Composite Score: {best['avg_composite']:.4f}")

        # Save to DB
        try:
            db = get_backend()
            registry = ModelRegistry(db)
            for _, row in comparison.iterrows():
                eid = registry.register_experiment(
                    model_type=row["model_type"],
                    params={},
                    train_seasons=["2021", "2122", "2223", "2324"],
                    test_season="2425",
                    metrics={
                        "accuracy": row["avg_accuracy"],
                        "brier_score": row["avg_brier"],
                        "roi": row["avg_roi"],
                        "composite_score": row["avg_composite"],
                    },
                )
            # Set best as active
            best_id = registry.get_best_model("composite_score")
            if best_id:
                registry.set_active(best_id)
            print(f"\n  ✅ Sonuçlar veritabanına kaydedildi.")
        except Exception as e:
            print(f"\n  ⚠️ DB kayıt hatası: {e}")

    print("=" * 60)


if __name__ == "__main__":
    main()
