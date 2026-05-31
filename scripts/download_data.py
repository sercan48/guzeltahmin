"""Download Football-Data.co.uk CSV files for all configured leagues and seasons."""

import sys
import os
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

import requests
from tqdm import tqdm

from config.settings import RAW_DIR, SEASONS, SEASON_LABELS
from config.leagues import LEAGUES, get_csv_url


def download_file(url: str, dest: Path) -> bool:
    """Download a file from URL to destination path."""
    try:
        response = requests.get(url, timeout=30)
        if response.status_code == 200 and len(response.content) > 100:
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(response.content)
            return True
        return False
    except requests.RequestException:
        return False


def download_all(season_filter: str = None):
    """Download CSVs for all leagues and seasons."""
    print("=" * 60)
    print("  Football-Data.co.uk CSV Downloader")
    print("=" * 60)

    seasons_to_download = [season_filter] if season_filter else SEASONS
    total = len(LEAGUES) * len(seasons_to_download)
    success = 0
    failed = []

    with tqdm(total=total, desc="Downloading") as pbar:
        for league_code, league in LEAGUES.items():
            for season in seasons_to_download:
                url = get_csv_url(season, league_code)
                dest = RAW_DIR / league_code / f"{season}.csv"

                # Always redownload active season (2526) to get latest match results
                if dest.exists() and season != "2526":
                    pbar.set_postfix_str(f"{league_code}/{season} (cached)")
                    success += 1
                    pbar.update(1)
                    continue

                ok = download_file(url, dest)
                if ok:
                    success += 1
                    pbar.set_postfix_str(f"{league_code}/{season} OK")
                else:
                    failed.append(f"{league_code}/{season}")
                    pbar.set_postfix_str(f"{league_code}/{season} FAIL")

                pbar.update(1)

    print(f"\n[OK] Downloaded: {success}/{total}")
    if failed:
        print(f"[WARN] Failed ({len(failed)}):")
        for f in failed:
            print(f"  - {f}")

    return success, failed


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--season", help="Season code (e.g. 2526)")
    args = parser.parse_args()
    download_all(season_filter=args.season)
