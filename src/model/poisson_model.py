"""Poisson regression for goal-based predictions (O/U, BTTS, score)."""

import pickle
import logging
from pathlib import Path

import numpy as np
from scipy.stats import poisson
from sklearn.linear_model import PoissonRegressor
from sklearn.metrics import mean_absolute_error

from config.settings import MODELS_DIR, RANDOM_SEED
from config.constants import LABEL_MAP

logger = logging.getLogger(__name__)


class PoissonGoalPredictor:
    """Dual Poisson regression: home_goals ~ X, away_goals ~ X."""

    def __init__(self):
        self.home_model = PoissonRegressor(alpha=0.5, max_iter=1000)
        self.away_model = PoissonRegressor(alpha=0.5, max_iter=1000)
        self._trained = False

    def train(self, X: np.ndarray, home_goals: np.ndarray,
              away_goals: np.ndarray) -> dict:
        """Train both Poisson models.

        Args:
            X: feature matrix
            home_goals: actual home goals
            away_goals: actual away goals

        Returns: training report dict
        """
        self.home_model.fit(X, home_goals)
        self.away_model.fit(X, away_goals)
        self._trained = True

        h_pred = self.home_model.predict(X)
        a_pred = self.away_model.predict(X)

        report = {
            "home_mae": round(mean_absolute_error(home_goals, h_pred), 4),
            "away_mae": round(mean_absolute_error(away_goals, a_pred), 4),
            "avg_home_lambda": round(float(np.mean(h_pred)), 3),
            "avg_away_lambda": round(float(np.mean(a_pred)), 3),
        }
        logger.info(f"[Poisson] Home MAE: {report['home_mae']}, Away MAE: {report['away_mae']}")
        return report

    def predict_lambdas(self, X: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """Predict expected goals (lambdas)."""
        if not self._trained:
            raise RuntimeError("Model not trained")
        return self.home_model.predict(X), self.away_model.predict(X)

    def predict_match(self, X_single: np.ndarray, n_simulations: int = 10000) -> dict:
        """Full match prediction via Monte Carlo simulation.

        Returns dict with 1X2 probs, O/U, BTTS, top scores.
        """
        X = X_single.reshape(1, -1) if X_single.ndim == 1 else X_single
        h_lambda = float(self.home_model.predict(X)[0])
        a_lambda = float(self.away_model.predict(X)[0])

        # Clamp lambdas
        h_lambda = max(0.1, min(h_lambda, 5.0))
        a_lambda = max(0.1, min(a_lambda, 5.0))

        # Monte Carlo simulation
        np.random.seed(RANDOM_SEED)
        h_goals = np.random.poisson(h_lambda, n_simulations)
        a_goals = np.random.poisson(a_lambda, n_simulations)

        # 1X2 probabilities
        home_wins = np.sum(h_goals > a_goals)
        draws = np.sum(h_goals == a_goals)
        away_wins = np.sum(h_goals < a_goals)

        h_prob = home_wins / n_simulations
        d_prob = draws / n_simulations
        a_prob = away_wins / n_simulations

        # Over/Under 2.5
        total_goals = h_goals + a_goals
        over25 = np.sum(total_goals > 2.5) / n_simulations
        under25 = 1.0 - over25

        # BTTS (both teams to score)
        btts = np.sum((h_goals > 0) & (a_goals > 0)) / n_simulations

        # Top score predictions
        from collections import Counter
        score_counts = Counter(zip(h_goals.tolist(), a_goals.tolist()))
        top_scores = [
            {"score": f"{s[0]}-{s[1]}", "prob": round(c / n_simulations, 4)}
            for s, c in score_counts.most_common(10)
        ]

        return {
            "home_lambda": round(h_lambda, 3),
            "away_lambda": round(a_lambda, 3),
            "h_prob": round(h_prob, 4),
            "d_prob": round(d_prob, 4),
            "a_prob": round(a_prob, 4),
            "over25_prob": round(over25, 4),
            "under25_prob": round(under25, 4),
            "btts_prob": round(btts, 4),
            "top_scores": top_scores,
            "predicted_result": ["H", "D", "A"][np.argmax([h_prob, d_prob, a_prob])],
        }

    def predict_proba_batch(self, X: np.ndarray) -> np.ndarray:
        """Batch 1X2 probabilities (fast analytical, no simulation)."""
        h_lambdas, a_lambdas = self.predict_lambdas(X)
        max_goals = 8
        probs = np.zeros((len(X), 3))

        for i in range(len(X)):
            hl, al = max(0.1, h_lambdas[i]), max(0.1, a_lambdas[i])
            h_pmf = poisson.pmf(np.arange(max_goals + 1), hl)
            a_pmf = poisson.pmf(np.arange(max_goals + 1), al)

            h_win, draw, a_win = 0.0, 0.0, 0.0
            for hg in range(max_goals + 1):
                for ag in range(max_goals + 1):
                    p = h_pmf[hg] * a_pmf[ag]
                    if hg > ag:
                        h_win += p
                    elif hg == ag:
                        draw += p
                    else:
                        a_win += p
            probs[i] = [h_win, draw, a_win]

        # Normalize
        row_sums = probs.sum(axis=1, keepdims=True)
        probs = probs / np.where(row_sums == 0, 1, row_sums)
        return probs

    def save(self, path: Path = None):
        path = path or (MODELS_DIR / "poisson_latest.pkl")
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "wb") as f:
            pickle.dump({"home": self.home_model, "away": self.away_model}, f)

    def load(self, path: Path = None):
        path = path or (MODELS_DIR / "poisson_latest.pkl")
        with open(path, "rb") as f:
            data = pickle.load(f)
        self.home_model = data["home"]
        self.away_model = data["away"]
        self._trained = True
