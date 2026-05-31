"""FIFA player attributes loader from Kaggle CSV datasets."""

import pandas as pd
from pathlib import Path

from config.settings import FIFA_DIR


# Expected column mapping from common Kaggle FIFA datasets
# We keep only one definitive mapping for position and market value to avoid duplicate columns
FIFA_COLUMN_MAP = {
    "short_name": "name",
    "long_name": "full_name",
    "overall": "fifa_overall",
    "pace": "fifa_pace",
    "shooting": "fifa_shooting",
    "passing": "fifa_passing",
    "dribbling": "fifa_dribbling",
    "defending": "fifa_defending",
    "physic": "fifa_physical",
    "club_name": "club",
    "player_positions": "position_raw",  # best source for position
    "value_eur": "market_value_eur",
    "nationality_name": "nationality",
    "age": "age",
}

POSITION_MAP = {
    "GK": "GK",
    "CB": "DEF", "RB": "DEF", "LB": "DEF", "RWB": "DEF", "LWB": "DEF",
    "CDM": "MID", "CM": "MID", "CAM": "MID", "RM": "MID", "LM": "MID",
    "RW": "FWD", "LW": "FWD", "CF": "FWD", "ST": "FWD", "RF": "FWD", "LF": "FWD",
}


def load_fifa_dataset(filepath: Path | None = None) -> pd.DataFrame:
    """Load FIFA player attributes from a Kaggle CSV.

    Automatically finds the most recent FIFA CSV in the data/fifa directory
    if no filepath is specified.
    """
    if filepath is None:
        csv_files = sorted(FIFA_DIR.glob("*.csv"), reverse=True)
        if not csv_files:
            print("[WARN] No FIFA CSV found in data/fifa/. Player attributes will be empty.")
            return pd.DataFrame()
        filepath = csv_files[0]
        print(f"[INFO] Using FIFA dataset: {filepath.name}")

    df = pd.read_csv(filepath, low_memory=False)

    # Rename columns
    rename_map = {k: v for k, v in FIFA_COLUMN_MAP.items() if k in df.columns}
    df.rename(columns=rename_map, inplace=True)

    return df


def standardize_positions(df: pd.DataFrame) -> pd.DataFrame:
    """Map detailed FIFA positions to our 4-tier system (GK/DEF/MID/FWD)."""
    if "position_raw" not in df.columns:
        df["position"] = "MID"  # Default fallback
        return df

    def map_position(pos_str: str) -> str:
        if pd.isna(pos_str):
            return "MID"
        primary = str(pos_str).split(",")[0].strip()
        return POSITION_MAP.get(primary, "MID")

    df["position"] = df["position_raw"].apply(map_position)
    return df


def parse_market_value(df: pd.DataFrame) -> pd.DataFrame:
    """Parse market value strings like '€110M' or raw EUR values."""
    if "market_value_eur" in df.columns:
        df["market_value"] = pd.to_numeric(df["market_value_eur"], errors="coerce") / 1_000_000
        return df

    if "market_value_raw" not in df.columns:
        df["market_value"] = 0.0
        return df

    def parse_val(v):
        if pd.isna(v):
            return 0.0
        v = str(v).replace("€", "").replace(",", "").strip()
        if v.endswith("M"):
            return float(v[:-1])
        elif v.endswith("K"):
            return float(v[:-1]) / 1000
        try:
            return float(v) / 1_000_000
        except ValueError:
            return 0.0

    df["market_value"] = df["market_value_raw"].apply(parse_val)
    return df


def prepare_fifa_data(filepath: Path | None = None) -> pd.DataFrame:
    """Full pipeline: load, standardize positions, parse values.

    Returns DataFrame with columns:
        name, club, position, fifa_overall, fifa_pace, fifa_shooting,
        fifa_passing, fifa_dribbling, fifa_defending, fifa_physical,
        market_value
    """
    df = load_fifa_dataset(filepath)
    if df.empty:
        return df

    df = standardize_positions(df)
    df = parse_market_value(df)

    # Ensure numeric columns
    numeric_cols = [
        "fifa_overall", "fifa_pace", "fifa_shooting",
        "fifa_passing", "fifa_dribbling", "fifa_defending", "fifa_physical",
    ]
    for col in numeric_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0).astype(int)

    # Select final columns
    keep_cols = ["name", "club", "position"] + numeric_cols + ["market_value"]
    available = [c for c in keep_cols if c in df.columns]
    df = df[available].copy()

    print(f"[OK] FIFA data: {len(df)} players loaded.")
    return df
