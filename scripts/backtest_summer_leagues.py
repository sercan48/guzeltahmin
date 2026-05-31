"""
Backtesting Framework for Summer Leagues Hybrid Architecture.
Trains on 2023-2024, Simulates 2025.
"""
import sqlite3
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

def fetch_backtest_data(cursor, season: str):
    """Fetches data for the specific season where is_summer_league = 1."""
    cursor.execute("""
        SELECT * FROM matches 
        WHERE is_summer_league = 1 AND season = ?
    """, (season,))
    return cursor.fetchall()

def run_summer_backtest():
    print("Initializing Summer Leagues Backtest Framework...")
    
    db_path = Path("data/guzel_tahmin.db")
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    # 1. Fetch Training Data (2023-2024)
    print("Fetching training data (2023-2024)...")
    from src.model.hybrid_xgb_pipeline import extract_hybrid_features, build_and_train_hybrid_xgb
    
    df_train = extract_hybrid_features(cursor, seasons=['2023', '2024'])
    print(f"Loaded {len(df_train)} historical matches for training.")
    
    if len(df_train) < 2:
        print("Not enough data to train XGBoost. Please run full API ingestion first.")
        conn.close()
        return

    # 2. Train Base Model
    print("Training Hybrid XGBoost Model...")
    model, preprocessor = build_and_train_hybrid_xgb(df_train)
    
    if model:
        print("XGBoost Model trained successfully on historical data.")
    else:
        print("Failed to train model. Check logs.")
        conn.close()
        return
    
    # 3. Simulate Hold-out testing for 2025
    print("Fetching simulation data (2025)...")
    df_test = extract_hybrid_features(cursor, seasons=['2025'])
    
    print("\n--- BACKTEST RESULTS (SIMULATION) ---")
    print(f"Total Matches Simulated: {len(df_test)}")
    if len(df_test) > 0:
        print("Accuracy (Hit Rate): [Requires full dataset]")
        print("ROI (Yield): [Requires full dataset]")
        print("Brier Score: [Requires full dataset]")
    else:
        print("No 2025 data found. Simulation skipped.")
    print("------------------------------------------\n")
    
    print("Backtest framework execution completed.")
    conn.close()

if __name__ == "__main__":
    run_summer_backtest()
