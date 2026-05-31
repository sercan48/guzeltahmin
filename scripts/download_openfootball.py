"""Download openfootball/football.json datasets for all configured leagues.

Fetches JSON files from GitHub raw URLs and caches them locally.
Covers 2010-2026 across 11 leagues (~176 files).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import requests
from tqdm import tqdm

from config.leagues import (
    LEAGUES, OPENFOOTBALL_SEASONS,
    get_openfootball_url,
)
from config.settings import RAW_DIR


OPENFOOTBALL_DIR = RAW_DIR / "openfootball"


def download_openfootball_file(url: str, dest: Path) -> bool:
    """Download a JSON file from GitHub."""
    try:
        response = requests.get(url, timeout=15)
        if response.status_code == 200:
            content = response.text.strip()
            if content.startswith("{") and len(content) > 50:
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_text(content, encoding="utf-8")
                return True
        return False
    except requests.RequestException:
        return False


def download_all_openfootball(
    leagues: list[str] = None,
    seasons: list[str] = None,
    force: bool = False,
):
    """Download all openfootball JSON files.

    Args:
        leagues: List of league codes (default: all with openfootball mapping)
        seasons: List of seasons (default: all 2010-2026)
        force: Re-download even if cached
    """
    if leagues is None:
        leagues = [code for code, lg in LEAGUES.items() if lg.openfootball_code]
    if seasons is None:
        seasons = OPENFOOTBALL_SEASONS

    print("=" * 60)
    print("  Openfootball JSON Downloader")
    print("=" * 60)
    print(f"  Leagues: {len(leagues)} | Seasons: {len(seasons)}")
    print(f"  Max files: {len(leagues) * len(seasons)}")
    print()

    total = len(leagues) * len(seasons)
    success = 0
    skipped = 0
    failed = []

    with tqdm(total=total, desc="Downloading", unit="file") as pbar:
        for league_code in leagues:
            league = LEAGUES[league_code]
            if not league.openfootball_code:
                pbar.update(len(seasons))
                continue

            for season in seasons:
                dest = OPENFOOTBALL_DIR / league_code / f"{season}.json"

                # Skip if cached
                if dest.exists() and not force:
                    success += 1
                    skipped += 1
                    pbar.set_postfix_str(f"{league_code}/{season} (cached)")
                    pbar.update(1)
                    continue

                url = get_openfootball_url(season, league_code)
                if not url:
                    pbar.update(1)
                    continue

                ok = download_openfootball_file(url, dest)
                if ok:
                    success += 1
                    pbar.set_postfix_str(f"{league_code}/{season} OK")
                else:
                    failed.append(f"{league_code}/{season}")
                    pbar.set_postfix_str(f"{league_code}/{season} -")

                pbar.update(1)

    print(f"\n[OK] Downloaded: {success}/{total} (cached: {skipped})")
    if failed:
        print(f"[INFO] Not available ({len(failed)}):")
        # Only show first 10
        for f in failed[:10]:
            print(f"  - {f}")
        if len(failed) > 10:
            print(f"  ... and {len(failed) - 10} more")

    return success, failed


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Download openfootball JSON data")
    parser.add_argument("--force", action="store_true", help="Re-download all files")
    parser.add_argument("--leagues", type=str, default=None,
                        help="Comma-separated league codes (default: all)")
    args = parser.parse_args()

    leagues = args.leagues.split(",") if args.leagues else None
    download_all_openfootball(leagues=leagues, force=args.force)
