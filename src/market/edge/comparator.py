"""Task 1 — Model vs Market Comparator.

Compares an *injected* model probability (the existing prediction model is NOT
touched, retrained, or called here) against the de-vigged market probability
from the R1.2 measurement layer.

Definitions (per match/market/selection)
----------------------------------------
    model_probability    p_model    (injected)
    market_probability   p_market   (de-vigged consensus from R1.2)
    probability_gap      g = p_model - p_market          (signed)
    probability_gap_zscore  z = (g - mu_g) / sigma_g
    probability_gap_percentile  = empirical percentile of g in the population

mu_g / sigma_g come from a reference population of gaps. By default the
population is the current evaluation batch (all selections of the same market);
historical (mu, sigma) can be injected to make z point-in-time stable.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, asdict
from typing import Dict, List, Optional, Tuple


@dataclass
class ComparatorResult:
    match_id: str
    market: str
    selection: str
    model_probability: float
    market_probability: float
    probability_gap: float
    probability_gap_zscore: Optional[float]
    probability_gap_percentile: Optional[float]

    def to_dict(self) -> dict:
        return asdict(self)


class ModelMarketComparator:
    """Pure comparator. No model calls, no learning."""

    def compare_batch(
        self,
        model_probs: Dict[Tuple[str, str, str], float],
        market_probs: Dict[Tuple[str, str, str], float],
        hist_gap_mean: Optional[float] = None,
        hist_gap_std: Optional[float] = None,
    ) -> Dict[Tuple[str, str, str], ComparatorResult]:
        keys = [k for k in model_probs if k in market_probs]
        gaps = {k: model_probs[k] - market_probs[k] for k in keys}

        if hist_gap_mean is not None and hist_gap_std:
            mu, sigma = hist_gap_mean, hist_gap_std
        else:
            mu, sigma = self._mean_std(list(gaps.values()))

        gap_values = sorted(gaps.values())
        out: Dict[Tuple[str, str, str], ComparatorResult] = {}
        for k in keys:
            g = gaps[k]
            z = (g - mu) / sigma if sigma and sigma > 1e-12 else None
            pct = self._percentile(gap_values, g)
            match_id, market, selection = k
            out[k] = ComparatorResult(
                match_id=match_id,
                market=market,
                selection=selection,
                model_probability=model_probs[k],
                market_probability=market_probs[k],
                probability_gap=g,
                probability_gap_zscore=z,
                probability_gap_percentile=pct,
            )
        return out

    @staticmethod
    def _mean_std(vals: List[float]) -> Tuple[float, float]:
        n = len(vals)
        if n == 0:
            return 0.0, 0.0
        mu = sum(vals) / n
        if n < 2:
            return mu, 0.0
        var = sum((v - mu) ** 2 for v in vals) / (n - 1)
        return mu, math.sqrt(var)

    @staticmethod
    def _percentile(sorted_vals: List[float], v: float) -> Optional[float]:
        n = len(sorted_vals)
        if n == 0:
            return None
        # fraction of population <= v (midpoint convention for ties)
        below = sum(1 for x in sorted_vals if x < v)
        equal = sum(1 for x in sorted_vals if x == v)
        return (below + 0.5 * equal) / n
