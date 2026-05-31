"""Openfootball JSON data loader — fetches and parses football.json datasets.

Covers 2010-2026 across 11 leagues, providing deep historical match data
without odds or referee info (those come from Football-Data.co.uk).
"""

import json
from pathlib import Path

import pandas as pd

from config.leagues import (
    LEAGUES, OPENFOOTBALL_SEASONS, OPENFOOTBALL_SEASON_MAP,
)
from config.settings import RAW_DIR


OPENFOOTBALL_DIR = RAW_DIR / "openfootball"


def parse_openfootball_json(filepath: Path, league_code: str, season_label: str) -> pd.DataFrame:
    """Parse a single openfootball JSON file into normalized DataFrame.

    JSON format:
        {"name": "...", "matches": [{"round": "Matchday 1", "date": "2024-08-16",
         "team1": "Team A", "team2": "Team B", "score": {"ft": [2, 1], "ht": [1, 0]}}]}
    """
    with open(filepath, "r", encoding="utf-8") as f:
        data = json.load(f)

    matches = data.get("matches", [])
    if not matches:
        return pd.DataFrame()

    rows = []
    for m in matches:
        score = m.get("score")
        if not score:
            continue

        ft = score.get("ft")
        if not ft or len(ft) < 2:
            continue

        ht = score.get("ht", [None, None])

        rows.append({
            "date": m.get("date", ""),
            "time": m.get("time", ""),
            "round": m.get("round", ""),
            "home_team": m.get("team1", ""),
            "away_team": m.get("team2", ""),
            "ft_home_goals": ft[0],
            "ft_away_goals": ft[1],
            "ht_home_goals": ht[0] if ht and len(ht) >= 2 else None,
            "ht_away_goals": ht[1] if ht and len(ht) >= 2 else None,
            "league_code": league_code,
            "season": season_label,
            "source": "openfootball",
        })

    df = pd.DataFrame(rows)
    if df.empty:
        return df

    # Derive full-time result
    df["ft_result"] = df.apply(
        lambda r: "H" if r["ft_home_goals"] > r["ft_away_goals"]
        else "A" if r["ft_home_goals"] < r["ft_away_goals"]
        else "D",
        axis=1,
    )

    # Parse date
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df = df.dropna(subset=["date"])
    df = df.sort_values("date").reset_index(drop=True)

    return df


def load_openfootball_season(league_code: str, season: str) -> pd.DataFrame:
    """Load a single league-season from cached openfootball JSON.

    Args:
        league_code: Internal code e.g. "E0"
        season: Openfootball format e.g. "2024-25"
    """
    league = LEAGUES.get(league_code)
    if not league or not league.openfootball_code:
        return pd.DataFrame()

    filepath = OPENFOOTBALL_DIR / league_code / f"{season}.json"
    if not filepath.exists():
        return pd.DataFrame()

    season_label = OPENFOOTBALL_SEASON_MAP.get(season, season)
    return parse_openfootball_json(filepath, league_code, season_label)


def load_all_openfootball(
    leagues: list[str] = None,
    seasons: list[str] = None,
) -> pd.DataFrame:
    """Load all cached openfootball data across leagues and seasons.

    Returns a combined DataFrame with all matches.
    """
    if leagues is None:
        leagues = [code for code, lg in LEAGUES.items() if lg.openfootball_code]
    if seasons is None:
        seasons = OPENFOOTBALL_SEASONS

    all_frames = []

    for league_code in leagues:
        for season in seasons:
            df = load_openfootball_season(league_code, season)
            if not df.empty:
                all_frames.append(df)

    if not all_frames:
        return pd.DataFrame()

    combined = pd.concat(all_frames, ignore_index=True)
    print(f"[openfootball] Total: {len(combined)} matches loaded")
    return combined
