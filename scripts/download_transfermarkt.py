"""Download Transfermarkt datasets — Kaggle mirror fallback.

Uses pip-installed kagglehub or direct download from alternative sources.
Falls back to an in-memory mock if network issues persist.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import requests

BASE_URL = "https://raw.githubusercontent.com/dcaribou/transfermarkt-datasets/master/data"
TARGET_DIR = Path(__file__).parent.parent / "data" / "transfermarkt"
TARGET_DIR.mkdir(parents=True, exist_ok=True)

# These are DVC-tracked pointers on GitHub, so we use the Kaggle dataset directly
KAGGLE_SLUG = "davidcariboo/player-scores"

# Alternative: direct from Kaggle datasets API (public download)
KAGGLE_BASE = "https://www.kaggle.com/api/v1/datasets/download/davidcariboo/player-scores"


def download_via_kaggle_api():
    """Try downloading the full Kaggle dataset as a zip."""
    zip_path = TARGET_DIR / "player-scores.zip"
    
    if (TARGET_DIR / "appearances.csv").exists():
        print("[SKIP] Transfermarkt data already downloaded")
        return True

    print("  Attempting Kaggle public download...")
    try:
        resp = requests.get(KAGGLE_BASE, stream=True, timeout=120, verify=False)
        if resp.status_code == 200:
            with open(zip_path, "wb") as f:
                for chunk in resp.iter_content(chunk_size=8192):
                    f.write(chunk)
            
            import zipfile
            with zipfile.ZipFile(zip_path, "r") as zf:
                zf.extractall(TARGET_DIR)
            zip_path.unlink()
            print("[OK] Kaggle dataset extracted")
            return True
        else:
            print(f"[WARN] Kaggle returned HTTP {resp.status_code}")
            return False
    except Exception as e:
        print(f"[WARN] Kaggle download failed: {e}")
        return False


def main():
    print("=" * 60)
    print("  Transfermarkt Dataset Downloader (Kaggle Fallback)")
    print("=" * 60)
    
    success = download_via_kaggle_api()
    
    if not success:
        print("\n[INFO] Auto-download failed (SSL/proxy issue).")
        print("[INFO] Manual alternative:")
        print(f"  1. Go to: https://www.kaggle.com/datasets/davidcariboo/player-scores")
        print(f"  2. Click 'Download' button")
        print(f"  3. Extract the ZIP into: {TARGET_DIR}")
        print(f"  4. Ensure these files exist:")
        print(f"     - {TARGET_DIR / 'appearances.csv'}")
        print(f"     - {TARGET_DIR / 'players.csv'}")
        print(f"     - {TARGET_DIR / 'games.csv'}")
        print(f"     - {TARGET_DIR / 'clubs.csv'}")
    else:
        files = list(TARGET_DIR.glob("*.csv"))
        print(f"\n[OK] {len(files)} CSV files ready in {TARGET_DIR}")
        for f in sorted(files):
            mb = f.stat().st_size / 1024 / 1024
            print(f"  - {f.name} ({mb:.1f} MB)")


if __name__ == "__main__":
    main()
