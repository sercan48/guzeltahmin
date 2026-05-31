"""Import Brazil Serie A matches and run predictions."""
import os
import sys
import requests
import urllib3
import pandas as pd
from pathlib import Path

# Suppress SSL warnings
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Add project root to python path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.db.base import get_backend
from src.ingestion.csv_loader import load_season
from src.preprocessing.schema_mapper import ingest_matches_to_db
from scripts.run_production_pipeline import run_production

RAW_DIR = Path("data/raw")
SCRATCH_DIR = Path("scratch")
BRA_RAW_DIR = RAW_DIR / "BRAZIL_SERIE_A"
BRA_RAW_DIR.mkdir(parents=True, exist_ok=True)

CSV_URL = "https://www.football-data.co.uk/new/BRA.csv"
SCRATCH_CSV = SCRATCH_DIR / "BRA.csv"

def download_brazil_csv() -> bool:
    """Download Brazil CSV directly or check scratch/BRA.csv mirror."""
    if SCRATCH_CSV.exists() and SCRATCH_CSV.stat().st_size > 20000:
        print(f"[INFO] Found local Brazil CSV at {SCRATCH_CSV}. Copying to raw directories...")
        return True

    print(f"Attempting to download Brazil CSV from: {CSV_URL}")
    try:
        # verify=False is used because BTK hijacking can return invalid SSL certificates
        resp = requests.get(CSV_URL, timeout=30, verify=False)
        if resp.status_code == 200:
            content = resp.text
            # Verify if it is a real CSV and not a BTK block HTML page
            if "erisime_engellenmis" in content or "<html" in content.lower():
                print("\n[!] DETECTED BTK BLOCK PAGE: The download was intercepted by the Turkish government block page.")
                print("[!] Lütfen bilgisayarınızda bir VPN (TunnelBear, ProtonVPN, vb.) açıp bu scripti tekrar çalıştırın.")
                print(f"[!] VEYA tarayıcınızda VPN açarak {CSV_URL} dosyasını indirin ve '{SCRATCH_CSV}' yoluna kaydedip scripti tekrar çalıştırın.")
                return False
            
            SCRATCH_CSV.parent.mkdir(parents=True, exist_ok=True)
            SCRATCH_CSV.write_text(content, encoding="utf-8")
            print("[OK] Brazil CSV downloaded successfully and cached in scratch/BRA.csv")
            return True
        else:
            print(f"[ERROR] Failed to download CSV, HTTP status code: {resp.status_code}")
            return False
    except Exception as e:
        print(f"[ERROR] Download failed: {e}")
        if SCRATCH_CSV.exists():
            print(f"[INFO] Fallback to existing local cached file at {SCRATCH_CSV}")
            return True
        return False

def main():
    print("=" * 60)
    print("  Brazil Serie A Data Importer & Predictor")
    print("=" * 60)

    # 1. Download/check CSV
    if not download_brazil_csv():
        sys.exit(1)

    # 2. Copy to all raw season directories so it can be loaded by standard functions
    csv_content = SCRATCH_CSV.read_bytes()
    seasons = ["2526", "2425", "2324", "2223", "2122", "2021"]
    for s in seasons:
        dest_path = BRA_RAW_DIR / f"{s}.csv"
        dest_path.write_bytes(csv_content)
    
    print("[OK] Brazil CSV distributed to raw season directories.")

    # 3. Load & Ingest into Database
    db = get_backend()
    db.connect()
    try:
        print("\n[1/2] Loading and parsing Brazil Serie A 2026 matches...")
        # load_season will filter only 2026 matches because of our custom logic
        df = load_season("2526", "BRAZIL_SERIE_A")
        
        if df is None or len(df) == 0:
            print("[WARN] No Brazil Serie A matches found for 2026 season.")
            return

        print(f"Loaded {len(df)} matches for Brazil Serie A 2026 season.")
        
        # Ingest into database
        print("[2/2] Ingesting matches to database...")
        inserted = ingest_matches_to_db(df, db)
        print(f"[OK] Ingested/updated {inserted} Brazil Serie A matches in database.")

    finally:
        db.close()

    # 4. Trigger Prediction pipeline to run Ensemble predictions on the new matches
    print("\n--- Running Production Prediction Pipeline ---")
    run_production()

if __name__ == "__main__":
    main()
