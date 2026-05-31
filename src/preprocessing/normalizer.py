"""Statistical normalization for features."""

import pandas as pd
import numpy as np


def min_max_normalize(series: pd.Series, feature_range: tuple = (0, 1)) -> pd.Series:
    """Normalize values to a specified range."""
    min_val, max_val = series.min(), series.max()
    if max_val == min_val:
        return pd.Series(np.full(len(series), feature_range[0]), index=series.index)
    scaled = (series - min_val) / (max_val - min_val)
    return scaled * (feature_range[1] - feature_range[0]) + feature_range[0]


def z_score_normalize(series: pd.Series) -> pd.Series:
    """Standardize to mean=0, std=1."""
    mean, std = series.mean(), series.std()
    if std == 0:
        return pd.Series(np.zeros(len(series)), index=series.index)
    return (series - mean) / std


def normalize_per_league(df: pd.DataFrame, columns: list[str], method: str = "minmax") -> pd.DataFrame:
    """Normalize columns within each league separately.

    This accounts for different scoring patterns across leagues
    (e.g., Serie A vs Eredivisie have very different goal averages).
    """
    result = df.copy()
    norm_fn = min_max_normalize if method == "minmax" else z_score_normalize

    for col in columns:
        if col not in df.columns:
            continue
        result[f"{col}_norm"] = df.groupby("league_code")[col].transform(norm_fn)

    return result


def normalize_odds(odds: float) -> float:
    """Convert decimal odds to implied probability."""
    if odds is None or odds <= 0:
        return 0.0
    return 1.0 / odds
