"""Walk-forward backtesting engine — 5 season validation."""

import logging
from pathlib import Path

import numpy as np
import pandas as pd

from config.settings import WALK_FORWARD_FOLDS, PROCESSED_DIR, RANDOM_SEED
from config.constants import FEATURE_COLUMNS, LABEL_MAP, LABEL_MAP_INV

logger = logging.getLogger(__name__)


class WalkForwardBacktester:
    """Walk-forward backtesting with expanding training window."""

    def __init__(self, features_dir: Path = None):
        self.features_dir = features_dir or PROCESSED_DIR

    def load_features(self, season: str) -> pd.DataFrame:
        """Load features for a specific season."""
        season_file = self.features_dir / f"features_{season}.csv"
        if season_file.exists():
            return pd.read_csv(season_file)
        # Fallback: filter from main features.csv
        main_file = self.features_dir / "features.csv"
        if main_file.exists():
            df = pd.read_csv(main_file)
            if "season" in df.columns:
                return df[df["season"] == season]
        return pd.DataFrame()

    def run_single_fold(self, model_type: str, train_seasons: list,
                        test_season: str) -> dict:
        """Train on train_seasons, predict on test_season."""
        # Load & concatenate training data
        train_dfs = [self.load_features(s) for s in train_seasons]
        train_dfs = [df for df in train_dfs if not df.empty]
        if not train_dfs:
            logger.error(f"No training data for seasons: {train_seasons}")
            return {"error": "no_training_data"}

        train_df = pd.concat(train_dfs, ignore_index=True)
        test_df = self.load_features(test_season)
        if test_df.empty:
            logger.error(f"No test data for season: {test_season}")
            return {"error": "no_test_data"}

        available = [c for c in FEATURE_COLUMNS if c in train_df.columns]
        if not available:
            return {"error": "no_features"}

        X_train = train_df[available].fillna(0).values
        y_train = train_df["ft_result"].map(LABEL_MAP).dropna().astype(int).values
        X_test = test_df[available].fillna(0).values
        y_test = test_df["ft_result"].map(LABEL_MAP).dropna().astype(int).values

        # Align lengths
        min_train = min(len(X_train), len(y_train))
        X_train, y_train = X_train[:min_train], y_train[:min_train]
        min_test = min(len(X_test), len(y_test))
        X_test, y_test = X_test[:min_test], y_test[:min_test]

        # Train model
        model, probs = self._train_and_predict(model_type, X_train, y_train, X_test, y_test, train_df)
        if model is None:
            return {"error": "training_failed"}

        predictions = np.argmax(probs, axis=1)

        # Compute metrics
        from src.evaluator.metrics_engine import MetricsEngine
        engine = MetricsEngine()

        # Get odds for ROI
        odds_df = None
        if "best_home_odds" in test_df.columns:
            odds_df = test_df[["best_home_odds", "best_draw_odds", "best_away_odds"]].iloc[:min_test]

        metrics = engine.calculate_all(y_test, predictions, probs, odds_df)

        # Per-league breakdown
        per_league = {}
        if "league_code" in test_df.columns:
            for league in test_df["league_code"].unique():
                mask = test_df["league_code"].values[:min_test] == league
                if mask.sum() > 0:
                    per_league[league] = {
                        "accuracy": round(float((predictions[mask] == y_test[mask]).mean()), 4),
                        "matches": int(mask.sum()),
                    }

        return {
            "fold": f"{'→'.join(train_seasons)}→{test_season}",
            "train_size": len(X_train),
            "test_size": len(X_test),
            "model_type": model_type,
            **metrics,
            "per_league": per_league,
        }

    def _train_and_predict(self, model_type, X_train, y_train, X_test, y_test, train_df):
        """Dispatch to correct model trainer."""
        try:
            if model_type == "xgboost":
                from src.model.trainer import train_model
                model, _, _ = train_model(X_train, y_train, X_test, y_test)
                return model, model.predict_proba(X_test)

            elif model_type == "lightgbm":
                from src.model.lightgbm_model import LightGBMTrainer
                trainer = LightGBMTrainer()
                model, _, _ = trainer.train(X_train, y_train, X_test, y_test)
                return model, model.predict_proba(X_test)

            elif model_type == "poisson":
                from src.model.poisson_model import PoissonGoalPredictor
                poi = PoissonGoalPredictor()
                hg = train_df["ft_home_goals"].fillna(0).values[:len(X_train)].astype(float)
                ag = train_df["ft_away_goals"].fillna(0).values[:len(X_train)].astype(float)
                poi.train(X_train, hg, ag)
                return poi, poi.predict_proba_batch(X_test)

            elif model_type == "ensemble":
                from src.model.ensemble import StackingEnsemble
                ens = StackingEnsemble()
                hg = train_df["ft_home_goals"].fillna(0).values[:len(X_train)].astype(float)
                ag = train_df["ft_away_goals"].fillna(0).values[:len(X_train)].astype(float)
                ens.train(X_train, y_train, hg, ag, n_folds=3)
                return ens, ens.predict_proba(X_test)

        except Exception as e:
            logger.error(f"Training failed for {model_type}: {e}")
            return None, None

    def run_walk_forward(self, model_type: str = "ensemble") -> list:
        """Run all folds from config."""
        results = []
        for fold in WALK_FORWARD_FOLDS:
            logger.info(f"Fold: train={fold['train']} → test={fold['test']}")
            result = self.run_single_fold(model_type, fold["train"], fold["test"])
            results.append(result)
            if "accuracy" in result:
                logger.info(f"  Accuracy: {result['accuracy']:.4f}")
        return results

    def compare_models(self, model_types: list = None) -> pd.DataFrame:
        """Run walk-forward for each model type, return comparison."""
        model_types = model_types or ["xgboost", "lightgbm", "poisson", "ensemble"]
        all_results = []

        for mt in model_types:
            logger.info(f"\n{'='*40} {mt.upper()} {'='*40}")
            folds = self.run_walk_forward(mt)
            valid = [f for f in folds if "error" not in f]
            if valid:
                avg = {
                    "model_type": mt,
                    "avg_accuracy": round(np.mean([f["accuracy"] for f in valid]), 4),
                    "avg_roi": round(np.mean([f.get("roi", 0) for f in valid]), 4),
                    "avg_brier": round(np.mean([f.get("brier_score", 0) for f in valid]), 4),
                    "avg_composite": round(np.mean([f.get("composite_score", 0) for f in valid]), 4),
                    "folds_completed": len(valid),
                }
                all_results.append(avg)

        return pd.DataFrame(all_results).sort_values("avg_composite", ascending=False)


def backtest_report(results: list) -> dict:
    """Aggregate fold results into summary."""
    valid = [r for r in results if "error" not in r]
    if not valid:
        return {"error": "no_valid_folds"}

    return {
        "total_folds": len(valid),
        "total_matches": sum(r.get("test_size", 0) for r in valid),
        "avg_accuracy": round(np.mean([r["accuracy"] for r in valid]), 4),
        "avg_roi": round(np.mean([r.get("roi", 0) for r in valid]), 4),
        "avg_brier": round(np.mean([r.get("brier_score", 0) for r in valid]), 4),
        "avg_log_loss": round(np.mean([r.get("log_loss", 0) for r in valid]), 4),
        "avg_composite": round(np.mean([r.get("composite_score", 0) for r in valid]), 4),
        "folds": valid,
    }


def print_backtest_report(report: dict):
    """Pretty-print backtest results in Turkish."""
    print(f"\n{'='*60}")
    print("  WALK-FORWARD BACKTEST RAPORU")
    print(f"{'='*60}")

    if "error" in report:
        print(f"  ❌ Hata: {report['error']}")
        return

    print(f"  Toplam Fold: {report['total_folds']}")
    print(f"  Toplam Maç: {report['total_matches']}")
    print(f"  Ortalama Doğruluk: %{report['avg_accuracy'] * 100:.1f}")
    print(f"  Ortalama ROI: %{report['avg_roi']:.1f}")
    print(f"  Ortalama Brier: {report['avg_brier']:.4f}")
    print(f"  Composite Score: {report['avg_composite']:.4f}")

    print(f"\n  {'Fold':<30} {'Acc':>7} {'ROI':>7} {'Brier':>7}")
    print(f"  {'-'*51}")
    for f in report.get("folds", []):
        fold_name = f.get("fold", "?")
        acc = f.get("accuracy", 0)
        roi = f.get("roi", 0)
        brier = f.get("brier_score", 0)
        print(f"  {fold_name:<30} {acc:>6.1%} {roi:>6.1f}% {brier:>7.4f}")

    print(f"{'='*60}")
