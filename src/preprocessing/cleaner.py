"""Data cleaning pipeline for match and player data."""

import pandas as pd
import numpy as np


def clean_match_data(df: pd.DataFrame) -> pd.DataFrame:
    """Clean raw match data from Football-Data CSV.

    - Remove matches with missing critical fields
    - Convert types
    - Handle NaN values
    """
    required_cols = ["home_team", "away_team", "ft_home_goals", "ft_away_goals", "ft_result"]
    missing = [c for c in required_cols if c not in df.columns]
    if missing:
        raise ValueError(f"Missing required columns: {missing}")

    # Drop rows where critical fields are missing
    df = df.dropna(subset=["home_team", "away_team", "ft_result"]).copy()

    # Convert goal columns to int
    goal_cols = [
        "ft_home_goals", "ft_away_goals",
        "ht_home_goals", "ht_away_goals",
    ]
    for col in goal_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0).astype(int)

    # Convert stat columns to int, filling missing with 0
    stat_cols = [
        "home_shots", "away_shots",
        "home_shots_target", "away_shots_target",
        "home_corners", "away_corners",
        "home_fouls", "away_fouls",
        "home_yellows", "away_yellows",
        "home_reds", "away_reds",
    ]
    for col in stat_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0).astype(int)

    # Trim whitespace from team names
    df["home_team"] = df["home_team"].str.strip()
    df["away_team"] = df["away_team"].str.strip()

    # Clean referee names
    if "referee" in df.columns:
        df["referee"] = df["referee"].fillna("Unknown").str.strip()

    return df


def remove_outliers(df: pd.DataFrame, column: str, z_threshold: float = 3.0) -> pd.DataFrame:
    """Remove statistical outliers based on z-score."""
    if column not in df.columns:
        return df

    values = pd.to_numeric(df[column], errors="coerce")
    mean = values.mean()
    std = values.std()

    if std == 0:
        return df

    z_scores = (values - mean).abs() / std
    return df[z_scores < z_threshold].copy()


def validate_results(df: pd.DataFrame) -> pd.DataFrame:
    """Ensure ft_result matches the actual goal difference."""
    mask_h = (df["ft_home_goals"] > df["ft_away_goals"]) & (df["ft_result"] != "H")
    mask_d = (df["ft_home_goals"] == df["ft_away_goals"]) & (df["ft_result"] != "D")
    mask_a = (df["ft_home_goals"] < df["ft_away_goals"]) & (df["ft_result"] != "A")

    inconsistent = mask_h | mask_d | mask_a
    if inconsistent.any():
        count = inconsistent.sum()
        print(f"[WARN] {count} matches with inconsistent result, fixing...")
        df.loc[mask_h, "ft_result"] = "H"
        df.loc[mask_d, "ft_result"] = "D"
        df.loc[mask_a, "ft_result"] = "A"

    return df


def full_clean_pipeline(df: pd.DataFrame) -> pd.DataFrame:
    """Run the complete cleaning pipeline."""
    initial = len(df)
    df = clean_match_data(df)
    df = validate_results(df)
    final = len(df)
    dropped = initial - final
    if dropped > 0:
        print(f"[INFO] Cleaned: {dropped} rows removed ({initial} → {final})")
    return df
