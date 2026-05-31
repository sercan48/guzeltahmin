"""Daily threshold runner and rollback guard.

Runs daily micro-adjustments and rollback checks.
Triggers full Optuna joint threshold optimizations weekly (or on --force).
"""

import sys
import argparse
from pathlib import Path
from datetime import datetime

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.db.base import get_backend
from src.model.adaptive_thresholds import AdaptiveThresholdOptimizer

def main():
    parser = argparse.ArgumentParser(description="Daily Threshold Runner & Rollback Safeguard.")
    parser.add_argument("--force", action="store_true", help="Force full weekly Optuna optimization for all leagues.")
    args = parser.parse_args()

    db = get_backend()
    db.connect()
    
    print("=" * 60)
    print("  Güzel Tahmin — Adaptive Threshold Runner")
    print("=" * 60)

    try:
        optimizer = AdaptiveThresholdOptimizer(db)

        # 1. Fetch active leagues in matches
        leagues = [r["league_code"] for r in db.fetchall("SELECT DISTINCT league_code FROM matches")]
        
        # Determine if we should trigger full optimization (e.g. Sunday or if forced)
        is_weekly_day = datetime.now().weekday() == 6 # Sunday
        run_full_opt = args.force or is_weekly_day

        print(f"Detected {len(leagues)} leagues. Full optimization run: {run_full_opt}")

        for league_id in leagues:
            print(f"\nProcessing league: {league_id}...")

            # 2. Check if active version exists, if not, perform initial optimization
            active = optimizer.get_active_thresholds(league_id)
            if not active:
                print(f"  [INIT] No active thresholds found for {league_id}. Running initial Optuna optimization...")
                res = optimizer.optimize_league(league_id, days=30, n_trials=30)
                print(f"  [INIT] Result: {res}")
                continue

            # 3. Check and run rollback guard
            print(f"  [GUARD] Running rollback safeguard check...")
            guard_res = optimizer.check_and_rollback_league(league_id)
            print(f"  [GUARD] Result: {guard_res}")

            if guard_res.get("status") == "rolled_back":
                # Skip micro-adjustments if we just rolled back
                continue

            # 4. Run daily micro-adjustments
            print(f"  [ADJUST] Running daily micro-adjustments...")
            adj_res = optimizer.run_daily_micro_adjustments(league_id)
            print(f"  [ADJUST] Result: {adj_res}")

            # 5. Weekly full optimization if scheduled
            if run_full_opt:
                print(f"  [OPT] Running full weekly joint Optuna optimization...")
                opt_res = optimizer.optimize_league(league_id, days=30, n_trials=50)
                print(f"  [OPT] Result: {opt_res}")

    except Exception as e:
        print(f"[ERROR] Runner failed: {e}")
    finally:
        db.close()

    print("\n" + "=" * 60)
    print("  Daily runner completed successfully!")
    print("=" * 60)

if __name__ == "__main__":
    main()
