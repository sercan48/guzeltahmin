"""Model versioning, comparison, and selection."""

import json
import pickle
import logging
from pathlib import Path
from datetime import datetime

from config.settings import MODELS_DIR

logger = logging.getLogger(__name__)


class ModelRegistry:
    """Manage model experiments and versioning."""

    def __init__(self, db, models_dir: Path = None):
        self.db = db
        self.models_dir = models_dir or MODELS_DIR
        self.models_dir.mkdir(parents=True, exist_ok=True)

    def register_experiment(self, model_type: str, params: dict,
                            train_seasons: list, test_season: str,
                            metrics: dict) -> int:
        """Save experiment to DB. Returns experiment_id."""
        self.db.execute("""
            INSERT INTO model_experiments
            (model_type, params_json, feature_set, train_seasons, test_season,
             accuracy, brier_score, log_loss, roi, yield_pct, composite_score)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            model_type,
            json.dumps(params, default=str),
            "v4_32features",
            json.dumps(train_seasons),
            test_season,
            metrics.get("accuracy", 0),
            metrics.get("brier_score", 0),
            metrics.get("log_loss", 0),
            metrics.get("roi", 0),
            metrics.get("yield_pct", 0),
            metrics.get("composite_score", 0),
        ))

        row = self.db.fetchone(
            "SELECT MAX(id) as eid FROM model_experiments WHERE model_type = ?",
            (model_type,)
        )
        eid = row["eid"] if row else 0
        logger.info(f"Experiment #{eid} registered: {model_type} → acc={metrics.get('accuracy', 0):.4f}")
        return eid

    def save_model(self, model, model_type: str, version: int = None) -> Path:
        """Save model to disk with versioning."""
        if version is None:
            existing = list(self.models_dir.glob(f"{model_type}_v*.pkl"))
            version = len(existing) + 1

        path = self.models_dir / f"{model_type}_v{version}.pkl"
        with open(path, "wb") as f:
            pickle.dump(model, f)

        # Also save as 'latest'
        latest = self.models_dir / f"{model_type}_latest.pkl"
        with open(latest, "wb") as f:
            pickle.dump(model, f)

        logger.info(f"Model saved: {path}")
        return path

    def load_model(self, model_type: str, version: str = "latest"):
        """Load model from disk."""
        if version == "latest":
            path = self.models_dir / f"{model_type}_latest.pkl"
        else:
            path = self.models_dir / f"{model_type}_v{version}.pkl"

        if not path.exists():
            raise FileNotFoundError(f"Model not found: {path}")

        with open(path, "rb") as f:
            return pickle.load(f)

    def load_active_model(self):
        """Load the model marked as active in DB."""
        row = self.db.fetchone(
            "SELECT model_type FROM model_experiments WHERE is_active = 1 ORDER BY id DESC LIMIT 1"
        )
        if not row:
            logger.warning("No active model found, defaulting to ensemble")
            return self.load_model("ensemble")
        return self.load_model(row["model_type"])

    def set_active(self, experiment_id: int):
        """Set a specific experiment as the active model."""
        self.db.execute("UPDATE model_experiments SET is_active = 0")
        self.db.execute(
            "UPDATE model_experiments SET is_active = 1 WHERE id = ?",
            (experiment_id,)
        )
        logger.info(f"Experiment #{experiment_id} set as active")

    def compare_experiments(self) -> list[dict]:
        """Get all experiments for comparison."""
        rows = self.db.fetchall("""
            SELECT id, model_type, accuracy, brier_score, log_loss, roi,
                   yield_pct, composite_score, is_active, test_season, created_at
            FROM model_experiments ORDER BY composite_score DESC
        """)
        return [dict(r) for r in rows] if rows else []

    def get_best_model(self, metric: str = "composite_score") -> int:
        """Get experiment_id with best metric."""
        row = self.db.fetchone(f"""
            SELECT id FROM model_experiments
            ORDER BY {metric} DESC LIMIT 1
        """)
        return row["id"] if row else 0
