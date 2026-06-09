"""Task 5 — Historical Validation Layer (evaluation only, NO retraining).

Given *settled* historical predictions (outcome known), measure edge quality
per bucket (tier and/or agreement class) using four orthogonal metrics. This
layer never fits, optimizes, or sizes anything — it scores past decisions
point-in-time so the kernel's tiers can be trusted.

Metrics (flat unit stake; no Kelly, no portfolio)
-------------------------------------------------
  ROI            = mean( outcome * entry_odds - 1 )            (profit per unit staked)
  CLV            = mean( entry_odds / closing_odds - 1 )       (closing line value)
  pct_beat_close = mean( entry_odds > closing_odds )
  Brier(model)   = mean( (p_model  - outcome)^2 )              (lower = sharper)
  Brier(market)  = mean( (p_market - outcome)^2 )              (reference)
  ECE            = sum_b (n_b/N) * |acc_b - conf_b|            (calibration error, K bins)

A trustworthy tier shows: ROI>0, CLV>0, Brier(model) <= Brier(market), low ECE.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict, field
from typing import Dict, List, Optional


@dataclass
class SettledRecord:
    match_id: str
    selection: str
    model_probability: float
    market_probability: float
    entry_odds: float
    closing_odds: float
    outcome: int                 # 1 = selection won, 0 = lost
    tier: str = ""
    agreement_class: str = ""


@dataclass
class BucketMetrics:
    bucket: str
    n: int
    roi: Optional[float]
    clv: Optional[float]
    pct_beat_close: Optional[float]
    brier_model: Optional[float]
    brier_market: Optional[float]
    calibration_error: Optional[float]

    def to_dict(self) -> dict:
        return asdict(self)


class HistoricalValidator:
    def __init__(self, ece_bins: int = 10) -> None:
        self.ece_bins = ece_bins

    def evaluate(
        self, records: List[SettledRecord], by: str = "tier"
    ) -> Dict[str, BucketMetrics]:
        """Bucket settled records by ``tier`` or ``agreement_class`` and score."""
        buckets: Dict[str, List[SettledRecord]] = {}
        for r in records:
            key = getattr(r, by) or "UNLABELED"
            buckets.setdefault(key, []).append(r)
        buckets["ALL"] = list(records)
        return {k: self._metrics(k, v) for k, v in buckets.items()}

    # -- per-bucket metrics -------------------------------------------------
    def _metrics(self, name: str, recs: List[SettledRecord]) -> BucketMetrics:
        n = len(recs)
        if n == 0:
            return BucketMetrics(name, 0, None, None, None, None, None, None)
        roi = sum(r.outcome * r.entry_odds - 1.0 for r in recs) / n
        clv = sum(r.entry_odds / r.closing_odds - 1.0 for r in recs if r.closing_odds) / n
        beat = sum(1 for r in recs if r.entry_odds > r.closing_odds) / n
        brier_m = sum((r.model_probability - r.outcome) ** 2 for r in recs) / n
        brier_k = sum((r.market_probability - r.outcome) ** 2 for r in recs) / n
        ece = self._ece(recs)
        return BucketMetrics(
            bucket=name, n=n,
            roi=roi, clv=clv, pct_beat_close=beat,
            brier_model=brier_m, brier_market=brier_k,
            calibration_error=ece,
        )

    def _ece(self, recs: List[SettledRecord]) -> float:
        n = len(recs)
        bins: List[List[SettledRecord]] = [[] for _ in range(self.ece_bins)]
        for r in recs:
            idx = min(self.ece_bins - 1, max(0, int(r.model_probability * self.ece_bins)))
            bins[idx].append(r)
        ece = 0.0
        for b in bins:
            if not b:
                continue
            conf = sum(r.model_probability for r in b) / len(b)
            acc = sum(r.outcome for r in b) / len(b)
            ece += (len(b) / n) * abs(acc - conf)
        return ece
