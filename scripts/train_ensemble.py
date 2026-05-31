"""Train the full Stacking Ensemble.

Usage:
    python scripts/train_ensemble.py
    python scripts/train_ensemble.py --seasons 2021,2122,2223,2324,2425
"""

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))

from config.settings import PROCESSED_DIR, MODELS_DIR
from config.constants import FEATURE_COLUMNS, LABEL_MAP
from src.model.ensemble import StackingEnsemble
from src.model.model_registry import ModelRegistry
from src.db.base import get_backend


def load_features(seasons: list) -> pd.DataFrame:
    """Load and concatenate features for given seasons."""
    dfs = []
    for season in seasons:
        path = PROCESSED_DIR / f"features_{season}.csv"
        if path.exists():
            dfs.append(pd.read_csv(path))

    if not dfs:
        main_path = PROCESSED_DIR / "features.csv"
        if main_path.exists():
            df = pd.read_csv(main_path)
            if "season" in df.columns:
                from config.settings import SEASON_LABELS
                full_seasons = [SEASON_LABELS.get(s, s) for s in seasons]
                df = df[df["season"].isin(full_seasons)]
            dfs.append(df)

    if not dfs:
        raise FileNotFoundError("No feature files found")

    return pd.concat(dfs, ignore_index=True)


def main():
    parser = argparse.ArgumentParser(description="Train Stacking Ensemble")
    parser.add_argument("--seasons", default="2021,2122,2223,2324,2425")
    args = parser.parse_args()

    seasons = [s.strip() for s in args.seasons.split(",")]

    print("=" * 60)
    print("  STACKING ENSEMBLE EĞİTİMİ")
    print("=" * 60)

    print(f"\n  Sezonlar: {', '.join(seasons)}")
    print(f"  Feature sayısı: {len(FEATURE_COLUMNS)}")

    # Load features
    df = load_features(seasons)
    print(f"  Toplam maç: {len(df)}")

    available = [c for c in FEATURE_COLUMNS if c in df.columns]
    print(f"  Mevcut feature: {len(available)}/{len(FEATURE_COLUMNS)}")

    # Filter out future matches or matches without results to prevent alignment shifts
    df = df[df["ft_result"].isin(["H", "D", "A"])].copy()

    X = df[available].fillna(0).values
    y = df["ft_result"].map(LABEL_MAP).astype(int).values

    # Goal columns for Poisson
    home_goals = df["ft_home_goals"].fillna(0).values.astype(float)
    away_goals = df["ft_away_goals"].fillna(0).values.astype(float)

    print(f"\n  Eğitim başlıyor ({len(X)} örnek)...")

    # Train ensemble
    ensemble = StackingEnsemble()
    league_codes = df["league_code"].values if "league_code" in df.columns else None
    report = ensemble.train(X, y, home_goals, away_goals, n_folds=5, league_codes=league_codes)

    print(f"\n  Sonuçlar:")
    print(f"    Ensemble Accuracy: %{report['ensemble_accuracy']*100:.1f}")
    print(f"    XGBoost OOF: %{report['xgb_oof_accuracy']*100:.1f}")
    print(f"    LightGBM OOF: %{report['lgb_oof_accuracy']*100:.1f}")
    print(f"    CatBoost OOF: %{report.get('cat_oof_accuracy', 0)*100:.1f}")
    print(f"    Poisson OOF: %{report['poi_oof_accuracy']*100:.1f}")

    # Save
    ensemble.save()
    print(f"\n  [OK] Ensemble kaydedildi: {MODELS_DIR / 'ensemble'}")

    # Register in DB
    try:
        db = get_backend()
        registry = ModelRegistry(db)
        eid = registry.register_experiment(
            model_type="ensemble",
            params={"n_folds": 5, "features": len(available)},
            train_seasons=seasons,
            test_season="live",
            metrics={
                "accuracy": report["ensemble_accuracy"],
                "composite_score": report["ensemble_accuracy"],
            },
        )
        registry.set_active(eid)
        print(f"  [OK] Experiment #{eid} kaydedildi ve aktif olarak ayarlandı.")
    except Exception as e:
        print(f"  [WARN] DB kayıt hatası: {e}")

    print("=" * 60)


if __name__ == "__main__":
    main()
