"""Football-Data.co.uk CSV loader and parser."""

import pandas as pd
from pathlib import Path

from config.constants import CSV_COLUMN_MAP
from config.leagues import LEAGUES, get_csv_url
from config.settings import RAW_DIR, SEASONS, SEASON_LABELS


def load_csv(filepath: Path) -> pd.DataFrame:
    """Load and standardize a single Football-Data CSV file.

    Handles encoding issues common with Football-Data files and
    renames columns to our internal standard names.
    """
    for encoding in ["utf-8", "latin-1", "cp1252"]:
        try:
            df = pd.read_csv(filepath, encoding=encoding)
            break
        except (UnicodeDecodeError, Exception):
            continue
    else:
        raise ValueError(f"Could not decode {filepath}")

    # Drop fully empty rows (Football-Data sometimes has trailing junk)
    df.dropna(how="all", inplace=True)

    # Rename known columns
    rename_map = {k: v for k, v in CSV_COLUMN_MAP.items() if k in df.columns}
    df.rename(columns=rename_map, inplace=True)

    return df


def load_season(season: str, league_code: str) -> pd.DataFrame | None:
    """Load CSV for a specific season and league from local raw directory.

    Args:
        season: Season code like '2425'
        league_code: League code like 'E0'

    Returns:
        DataFrame with standardized columns, or None if file doesn't exist.
    """
    filepath = RAW_DIR / league_code / f"{season}.csv"
    if not filepath.exists():
        return None

    df = load_csv(filepath)
    
    if league_code in ("NORWAY_ELITESERIEN", "BRAZIL_SERIE_A"):
        df = parse_date(df)
        season_year_map = {
            "2526": 2026,
            "2425": 2025,
            "2324": 2024,
            "2223": 2023,
            "2122": 2022,
            "2021": 2021
        }
        target_year = season_year_map.get(season)
        if target_year:
            df = df[df["date"].dt.year == target_year].copy()
            df["season"] = str(target_year)
        else:
            df["season"] = season
    else:
        df["season"] = SEASON_LABELS.get(season, season)

    df["league_code"] = league_code

    return df


def load_all_data() -> pd.DataFrame:
    """Load all available CSV data across all leagues and seasons.

    Returns:
        Combined DataFrame with all match data.
    """
    frames = []

    for league_code in LEAGUES:
        for season in SEASONS:
            df = load_season(season, league_code)
            if df is not None and len(df) > 0:
                frames.append(df)
                print(f"  [OK] {league_code} {SEASON_LABELS.get(season, season)}: {len(df)} matches")
            else:
                print(f"  [--] {league_code} {SEASON_LABELS.get(season, season)}: not found")

    if not frames:
        raise FileNotFoundError("No CSV data found. Run scripts/download_data.py first.")

    combined = pd.concat(frames, ignore_index=True)
    print(f"\n[OK] Total: {len(combined)} matches loaded.")
    return combined


def parse_date(df: pd.DataFrame) -> pd.DataFrame:
    """Parse date column handling multiple formats from Football-Data."""
    date_col = df["date"].copy()

    # Try dd/mm/yyyy first, then dd/mm/yy
    parsed = pd.to_datetime(date_col, format="%d/%m/%Y", errors="coerce")
    mask = parsed.isna()
    if mask.any():
        parsed[mask] = pd.to_datetime(date_col[mask], format="%d/%m/%y", errors="coerce")

    df["date"] = parsed
    return df


def extract_odds(df: pd.DataFrame) -> pd.DataFrame:
    """Extract and standardize odds columns from CSV data.

    Prioritizes Pinnacle (sharpest) then Bet365.
    Falls back to market average if specific bookmaker not available.
    """
    odds_df = pd.DataFrame(index=df.index)

    # Home odds (prefer Pinnacle > Bet365)
    if "pin_home" in df.columns:
        odds_df["home_odds"] = pd.to_numeric(df["pin_home"], errors="coerce")
        odds_df["bookmaker"] = "Pinnacle"
    elif "b365_home" in df.columns:
        odds_df["home_odds"] = pd.to_numeric(df["b365_home"], errors="coerce")
        odds_df["bookmaker"] = "Bet365"
    else:
        odds_df["home_odds"] = None
        odds_df["bookmaker"] = "Unknown"

    # Draw odds
    if "pin_draw" in df.columns:
        odds_df["draw_odds"] = pd.to_numeric(df["pin_draw"], errors="coerce")
    elif "b365_draw" in df.columns:
        odds_df["draw_odds"] = pd.to_numeric(df["b365_draw"], errors="coerce")

    # Away odds
    if "pin_away" in df.columns:
        odds_df["away_odds"] = pd.to_numeric(df["pin_away"], errors="coerce")
    elif "b365_away" in df.columns:
        odds_df["away_odds"] = pd.to_numeric(df["b365_away"], errors="coerce")

    # Over/Under 2.5
    if "avg_over25" in df.columns:
        odds_df["over25_odds"] = pd.to_numeric(df["avg_over25"], errors="coerce")
    if "avg_under25" in df.columns:
        odds_df["under25_odds"] = pd.to_numeric(df["avg_under25"], errors="coerce")

    return odds_df
