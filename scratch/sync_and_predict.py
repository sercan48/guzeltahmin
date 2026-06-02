import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

import logging
logging.basicConfig(level=logging.INFO)

from scripts.download_data import download_all
from src.db.base import get_backend
from src.ingestion.csv_loader import load_season
from src.preprocessing.schema_mapper import ingest_matches_to_db
from config.leagues import LEAGUES
from scripts.run_production_pipeline import run_production

print("Step 1: Downloading active season CSVs...")
download_all(season_filter="2526")

print("Step 2: Ingesting into database...")
db = get_backend()
db.connect()
try:
    total_inserted = 0
    for league_code in LEAGUES:
        df = load_season("2526", league_code)
        if df is not None and len(df) > 0:
            count = ingest_matches_to_db(df, db)
            total_inserted += count
    print(f"Daily DB sync complete. Ingested/updated {total_inserted} matches.")
finally:
    db.close()

print("Step 3: Running production prediction pipeline...")
run_production()
print("All steps completed successfully!")
