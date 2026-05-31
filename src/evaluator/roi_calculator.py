"""ROI and betting simulation calculator."""

import numpy as np
import logging

from config.constants import LABEL_MAP_INV

logger = logging.getLogger(__name__)


class ROICalculator:
    """Simulated betting ROI with multiple strategies."""

    def __init__(self, unit_stake: float = 100.0):
        self.unit_stake = unit_stake

    def calculate_flat_roi(self, predictions_df) -> dict:
        """Flat betting: bet unit_stake on every predicted result."""
        total_staked = 0.0
        total_returns = 0.0
        n_bets = 0
        wins = 0

        for _, row in predictions_df.iterrows():
            pred = row.get("predicted_result", "")
            actual = row.get("ft_result", row.get("actual_result", ""))
            odds = self._get_odds_for_result(row, pred)

            if odds is None or odds <= 1.0:
                continue

            total_staked += self.unit_stake
            n_bets += 1

            if pred == actual:
                total_returns += self.unit_stake * odds
                wins += 1

        roi = ((total_returns - total_staked) / total_staked * 100) if total_staked > 0 else 0
        yield_pct = roi / max(n_bets, 1)

        return {
            "total_staked": round(total_staked, 2),
            "total_returns": round(total_returns, 2),
            "profit": round(total_returns - total_staked, 2),
            "roi_pct": round(roi, 2),
            "yield_pct": round(yield_pct, 2),
            "n_bets": n_bets,
            "wins": wins,
            "win_rate": round(wins / max(n_bets, 1) * 100, 1),
        }

    def calculate_value_roi(self, predictions_df, min_margin: float = 3.0) -> dict:
        """Only bet when model sees value (implied prob gap > margin)."""
        total_staked = 0.0
        total_returns = 0.0
        n_bets = 0
        n_value = 0
        wins = 0

        for _, row in predictions_df.iterrows():
            pred = row.get("predicted_result", "")
            actual = row.get("ft_result", row.get("actual_result", ""))
            odds = self._get_odds_for_result(row, pred)
            model_prob = self._get_model_prob(row, pred)

            if odds is None or odds <= 1.0 or model_prob is None:
                continue

            implied_prob = 1.0 / odds
            margin = (model_prob - implied_prob) * 100

            if margin < min_margin:
                continue

            n_value += 1
            total_staked += self.unit_stake
            n_bets += 1

            if pred == actual:
                total_returns += self.unit_stake * odds
                wins += 1

        roi = ((total_returns - total_staked) / total_staked * 100) if total_staked > 0 else 0

        return {
            "total_staked": round(total_staked, 2),
            "total_returns": round(total_returns, 2),
            "profit": round(total_returns - total_staked, 2),
            "roi_pct": round(roi, 2),
            "yield_pct": round(roi / max(n_bets, 1), 2),
            "n_bets": n_bets,
            "n_value_bets": n_value,
            "wins": wins,
        }

    def calculate_kelly_roi(self, predictions_df, kelly_fraction: float = 0.25) -> dict:
        """Kelly criterion optimal stake sizing."""
        total_staked = 0.0
        total_returns = 0.0
        n_bets = 0
        stakes = []
        bankroll = 10000.0

        for _, row in predictions_df.iterrows():
            pred = row.get("predicted_result", "")
            actual = row.get("ft_result", row.get("actual_result", ""))
            odds = self._get_odds_for_result(row, pred)
            prob = self._get_model_prob(row, pred)

            if odds is None or odds <= 1.0 or prob is None or prob <= 0:
                continue

            # Kelly formula: f = (p*b - q) / b where b = odds-1, q = 1-p
            b = odds - 1
            q = 1 - prob
            kelly = (prob * b - q) / b if b > 0 else 0

            if kelly <= 0:
                continue

            stake = bankroll * kelly * kelly_fraction
            stake = max(1, min(stake, bankroll * 0.1))

            total_staked += stake
            stakes.append(stake)
            n_bets += 1

            if pred == actual:
                total_returns += stake * odds
                bankroll += stake * (odds - 1)
            else:
                bankroll -= stake

        roi = ((total_returns - total_staked) / total_staked * 100) if total_staked > 0 else 0

        return {
            "total_staked": round(total_staked, 2),
            "total_returns": round(total_returns, 2),
            "roi_pct": round(roi, 2),
            "n_bets": n_bets,
            "avg_stake": round(np.mean(stakes), 2) if stakes else 0,
            "final_bankroll": round(bankroll, 2),
        }

    def max_drawdown(self, predictions_df) -> dict:
        """Calculate maximum drawdown streak."""
        streak = 0
        max_streak = 0
        cumulative = []
        running = 0.0

        for _, row in predictions_df.iterrows():
            pred = row.get("predicted_result", "")
            actual = row.get("ft_result", row.get("actual_result", ""))
            odds = self._get_odds_for_result(row, pred)

            if odds is None:
                continue

            if pred == actual:
                running += self.unit_stake * (odds - 1)
                streak = 0
            else:
                running -= self.unit_stake
                streak += 1
                max_streak = max(max_streak, streak)

            cumulative.append(running)

        peak = 0
        max_dd = 0
        for val in cumulative:
            peak = max(peak, val)
            dd = peak - val
            max_dd = max(max_dd, dd)

        return {
            "max_consecutive_losses": max_streak,
            "max_drawdown_amount": round(max_dd, 2),
            "final_pnl": round(running, 2),
        }

    def _get_odds_for_result(self, row, result: str):
        odds_map = {
            "H": ["best_home_odds", "b365_home", "pin_home", "home_odds"],
            "D": ["best_draw_odds", "b365_draw", "pin_draw", "draw_odds"],
            "A": ["best_away_odds", "b365_away", "pin_away", "away_odds"],
        }
        for col in odds_map.get(result, []):
            val = row.get(col)
            if val is not None and val > 1.0:
                return float(val)
        return None

    def _get_model_prob(self, row, result: str):
        prob_map = {"H": "pred_home_prob", "D": "pred_draw_prob", "A": "pred_away_prob"}
        alt_map = {"H": "home_win_prob", "D": "draw_prob", "A": "away_win_prob"}

        for pm in [prob_map, alt_map]:
            col = pm.get(result)
            if col and row.get(col) is not None:
                return float(row[col])
        return None
