"""Stacking Ensemble — combines XGBoost + LightGBM + CatBoost + Poisson via meta-learner and League Residual Layer."""

import pickle
import logging
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import StratifiedKFold
from sklearn.metrics import accuracy_score
from catboost import CatBoostClassifier

from config.settings import MODELS_DIR, RANDOM_SEED
from config.constants import LABEL_MAP, LABEL_MAP_INV

logger = logging.getLogger(__name__)


class StackingEnsemble:
    """Level-0: XGBoost + LightGBM + CatBoost + Poisson, Level-1: Logistic Regression + League Residuals."""

    def __init__(self):
        self.xgb_model = None
        self.lgb_model = None
        self.cat_model = None
        self.poisson_model = None
        self.meta_learner = LogisticRegression(
            max_iter=1000, C=1.0, random_state=RANDOM_SEED
        )
        self.league_residuals = {}  # League-specific residual correctors
        self._trained = False

    def train(self, X: np.ndarray, y: np.ndarray,
              home_goals: np.ndarray = None, away_goals: np.ndarray = None,
              n_folds: int = 5, league_codes: np.ndarray = None) -> dict:
        """Train ensemble with out-of-fold stacking and league residual adjustment."""
        from src.model.trainer import train_model as train_xgb
        from src.model.lightgbm_model import LightGBMTrainer
        from src.model.poisson_model import PoissonGoalPredictor

        kf = StratifiedKFold(n_splits=n_folds, shuffle=True, random_state=RANDOM_SEED)
        n_samples = len(X)
        oof_xgb = np.zeros((n_samples, 3))
        oof_lgb = np.zeros((n_samples, 3))
        oof_cat = np.zeros((n_samples, 3))
        oof_poi = np.zeros((n_samples, 3))

        logger.info(f"[Ensemble] Training {n_folds}-fold stacking on {n_samples} samples")

        for fold, (train_idx, val_idx) in enumerate(kf.split(X, y)):
            X_tr, X_val = X[train_idx], X[val_idx]
            y_tr, y_val = y[train_idx], y[val_idx]

            # XGBoost
            xgb_m, _, _ = train_xgb(X_tr, y_tr, X_val, y_val)
            oof_xgb[val_idx] = xgb_m.predict_proba(X_val)

            # LightGBM
            lgb_trainer = LightGBMTrainer()
            lgb_m, _, _ = lgb_trainer.train(X_tr, y_tr, X_val, y_val)
            oof_lgb[val_idx] = lgb_m.predict_proba(X_val)

            # CatBoost
            cat_m = CatBoostClassifier(
                iterations=250,
                learning_rate=0.05,
                depth=5,
                loss_function='MultiClass',
                random_seed=RANDOM_SEED,
                verbose=0
            )
            cat_m.fit(X_tr, y_tr, eval_set=(X_val, y_val), early_stopping_rounds=20)
            oof_cat[val_idx] = cat_m.predict_proba(X_val)

            # Poisson
            if home_goals is not None and away_goals is not None:
                poi = PoissonGoalPredictor()
                poi.train(X_tr, home_goals[train_idx], away_goals[train_idx])
                oof_poi[val_idx] = poi.predict_proba_batch(X_val)
            else:
                oof_poi[val_idx] = oof_xgb[val_idx]  # fallback

            logger.info(f"  Fold {fold+1}/{n_folds} done")

        # Train meta-learner on OOF predictions
        meta_features = np.hstack([oof_xgb, oof_lgb, oof_cat, oof_poi])
        self.meta_learner.fit(meta_features, y)

        meta_pred = self.meta_learner.predict(meta_features)
        meta_acc = accuracy_score(y, meta_pred)

        # Train final base models on ALL data
        self.xgb_model, xgb_acc, _ = train_xgb(X, y, X, y)
        
        lgb_final = LightGBMTrainer()
        self.lgb_model, lgb_acc, _ = lgb_final.train(X, y, X, y)

        self.cat_model = CatBoostClassifier(
            iterations=250,
            learning_rate=0.05,
            depth=5,
            loss_function='MultiClass',
            random_seed=RANDOM_SEED,
            verbose=0
        )
        self.cat_model.fit(X, y)

        self.poisson_model = PoissonGoalPredictor()
        if home_goals is not None:
            self.poisson_model.train(X, home_goals, away_goals)

        # Stacking predictions on full OOF to train League Residuals
        oof_ensemble_probs = self.meta_learner.predict_proba(meta_features)
        self.oof_ensemble_probs = oof_ensemble_probs

        # Train league-specific residual layers
        self.league_residuals = {}
        if league_codes is not None:
            summer_leagues = ["NORWAY_ELITESERIEN", "BRAZIL_SERIE_A", "SWEDEN_ALLSVENSKAN", "FINLAND_VEIKKAUSLIIGA", "USA_MLS"]
            for league in summer_leagues:
                # Find indices of matches in this league
                idx = np.where(league_codes == league)[0]
                if len(idx) >= 15:
                    logger.info(f"[Ensemble] Training League Residual Layer for {league} ({len(idx)} matches)")
                    res_model = LogisticRegression(C=0.5, max_iter=1000, random_state=RANDOM_SEED)
                    res_model.fit(oof_ensemble_probs[idx], y[idx])
                    self.league_residuals[league] = res_model
                    
                    # Log improvement
                    global_acc = accuracy_score(y[idx], np.argmax(oof_ensemble_probs[idx], axis=1))
                    res_probs = res_model.predict_proba(oof_ensemble_probs[idx])
                    res_acc = accuracy_score(y[idx], np.argmax(res_probs, axis=1))
                    logger.info(f"  {league} Residual Layer Accuracy: Global Stacking: {global_acc:.4f} -> Residual Corrected: {res_acc:.4f}")

        self._trained = True

        report = {
            "ensemble_accuracy": round(meta_acc, 4),
            "xgb_oof_accuracy": round(accuracy_score(y, np.argmax(oof_xgb, axis=1)), 4),
            "lgb_oof_accuracy": round(accuracy_score(y, np.argmax(oof_lgb, axis=1)), 4),
            "cat_oof_accuracy": round(accuracy_score(y, np.argmax(oof_cat, axis=1)), 4),
            "poi_oof_accuracy": round(accuracy_score(y, np.argmax(oof_poi, axis=1)), 4),
        }
        logger.info(f"[Ensemble] Accuracies: {report}")
        return report

    def predict(self, X: np.ndarray, league_code: str = None) -> dict:
        """Predict with ensemble and league-specific residual adjustments."""
        if not self._trained:
            raise RuntimeError("Ensemble not trained")

        X = X.reshape(1, -1) if X.ndim == 1 else X

        xgb_probs = self.xgb_model.predict_proba(X)
        lgb_probs = self.lgb_model.predict_proba(X)
        cat_probs = self.cat_model.predict_proba(X)
        poi_probs = self.poisson_model.predict_proba_batch(X)

        meta_features = np.hstack([xgb_probs, lgb_probs, cat_probs, poi_probs])
        ensemble_probs = self.meta_learner.predict_proba(meta_features)

        # Apply league residual layer if applicable
        if league_code and league_code in self.league_residuals:
            ensemble_probs = self.league_residuals[league_code].predict_proba(ensemble_probs)

        # Model agreement (how well 3 models agree on Argmax)
        agreement = self._model_agreement(xgb_probs, lgb_probs, cat_probs)

        results = []
        for i in range(len(X)):
            pred_idx = np.argmax(ensemble_probs[i])
            results.append({
                "h_prob": round(float(ensemble_probs[i][0]), 4),
                "d_prob": round(float(ensemble_probs[i][1]), 4),
                "a_prob": round(float(ensemble_probs[i][2]), 4),
                "predicted_result": LABEL_MAP_INV[pred_idx],
                "confidence": round(float(np.max(ensemble_probs[i])) * 100, 1),
                "model_agreement": round(float(agreement[i]), 3),
                "xgb_probs": xgb_probs[i].tolist(),
                "lgb_probs": lgb_probs[i].tolist(),
                "cat_probs": cat_probs[i].tolist(),
                "poi_probs": poi_probs[i].tolist(),
            })

        return results[0] if len(results) == 1 else results

    def predict_proba(self, X: np.ndarray, league_code: str = None) -> np.ndarray:
        """Batch probability prediction for backtesting, with optional league correction."""
        if not self._trained:
            raise RuntimeError("Ensemble not trained")

        xgb_p = self.xgb_model.predict_proba(X)
        lgb_p = self.lgb_model.predict_proba(X)
        cat_p = self.cat_model.predict_proba(X)
        poi_p = self.poisson_model.predict_proba_batch(X)

        meta_f = np.hstack([xgb_p, lgb_p, cat_p, poi_p])
        ensemble_probs = self.meta_learner.predict_proba(meta_f)

        if league_code and league_code in self.league_residuals:
            ensemble_probs = self.league_residuals[league_code].predict_proba(ensemble_probs)

        return ensemble_probs

    def predict_full(self, X_single: np.ndarray, league_code: str = None) -> dict:
        """Full prediction including Poisson outputs (O/U, BTTS, scores) and residuals."""
        base = self.predict(X_single, league_code=league_code)
        poisson_detail = self.poisson_model.predict_match(X_single)

        base.update({
            "over25_prob": poisson_detail["over25_prob"],
            "under25_prob": poisson_detail["under25_prob"],
            "btts_prob": poisson_detail["btts_prob"],
            "top_scores": poisson_detail["top_scores"],
            "home_lambda": poisson_detail["home_lambda"],
            "away_lambda": poisson_detail["away_lambda"],
        })
        return base

    def _model_agreement(self, xgb_p, lgb_p, cat_p) -> np.ndarray:
        """Measure agreement between base models (0-1, higher = more agreement)."""
        xgb_pred = np.argmax(xgb_p, axis=1)
        lgb_pred = np.argmax(lgb_p, axis=1)
        cat_pred = np.argmax(cat_p, axis=1)

        agreement = np.zeros(len(xgb_pred))
        for i in range(len(xgb_pred)):
            preds = [xgb_pred[i], lgb_pred[i], cat_pred[i]]
            max_count = max(preds.count(p) for p in set(preds))
            agreement[i] = max_count / 3.0
        return agreement

    def save(self, path: Path = None):
        path = path or (MODELS_DIR / "ensemble")
        path.mkdir(parents=True, exist_ok=True)
        with open(path / "xgb.pkl", "wb") as f:
            pickle.dump(self.xgb_model, f)
        with open(path / "lgb.pkl", "wb") as f:
            pickle.dump(self.lgb_model, f)
        with open(path / "cat.pkl", "wb") as f:
            pickle.dump(self.cat_model, f)
        with open(path / "poisson.pkl", "wb") as f:
            pickle.dump({"home": self.poisson_model.home_model,
                         "away": self.poisson_model.away_model}, f)
        with open(path / "meta.pkl", "wb") as f:
            pickle.dump(self.meta_learner, f)
        with open(path / "residuals.pkl", "wb") as f:
            pickle.dump(self.league_residuals, f)
        if hasattr(self, "oof_ensemble_probs") and self.oof_ensemble_probs is not None:
            np.save(path / "oof_probs.npy", self.oof_ensemble_probs)
        logger.info(f"Ensemble saved to {path}")

    def load(self, path: Path = None):
        path = path or (MODELS_DIR / "ensemble")
        with open(path / "xgb.pkl", "rb") as f:
            self.xgb_model = pickle.load(f)
        with open(path / "lgb.pkl", "rb") as f:
            self.lgb_model = pickle.load(f)
        with open(path / "cat.pkl", "rb") as f:
            self.cat_model = pickle.load(f)
        with open(path / "poisson.pkl", "rb") as f:
            data = pickle.load(f)
            from src.model.poisson_model import PoissonGoalPredictor
            self.poisson_model = PoissonGoalPredictor()
            self.poisson_model.home_model = data["home"]
            self.poisson_model.away_model = data["away"]
            self.poisson_model._trained = True
        with open(path / "meta.pkl", "rb") as f:
            self.meta_learner = pickle.load(f)
        residuals_path = path / "residuals.pkl"
        if residuals_path.exists():
            with open(residuals_path, "rb") as f:
                self.league_residuals = pickle.load(f)
        else:
            self.league_residuals = {}
        
        # Load OOF probabilities if they exist
        oof_path = path / "oof_probs.npy"
        if oof_path.exists():
            self.oof_ensemble_probs = np.load(oof_path)
        else:
            self.oof_ensemble_probs = None
            
        self._trained = True
        logger.info(f"Ensemble loaded from {path}")
