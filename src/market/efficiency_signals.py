"""Task 4 — Market Efficiency Signals.

NO ML. Cross-bookmaker microstructure measures computed in de-vigged
probability space.

De-vigging (per book, per market, per horizon)
----------------------------------------------
For bookmaker b with selections {s} of a market:
    p_raw[b,s] = 1 / odds[b,s]
    overround R[b] = sum_s p_raw[b,s]            # >= 1.0 (the vig)
    p[b,s] = p_raw[b,s] / R[b]                    # fair, sums to 1

Consensus & disagreement (per selection, current horizon)
---------------------------------------------------------
    consensus_prob[s]            = mean_b p[b,s]
    disagreement[s]              = stdev_b p[b,s]
    market_consensus_score       = clip(1 - mean_s(disagreement[s]/consensus_prob[s]), 0, 1)
    bookmaker_disagreement_index  = mean_s disagreement[s]

Sharp-proxy (EARLY movement only — no close, no leakage)
--------------------------------------------------------
Uses only opening -> 24h move, weighted by book confidence (sharp books carry
higher confidence_score in the R1.1 mapping):
    move[b,s]        = p_24[b,s] - p_open[b,s]            # +ve => shortened
    sharp_proxy[s]   = sum_b conf_b * move[b,s] / sum_b conf_b
    sharp_proxy_signal = sharp_proxy of the selection with max |sharp_proxy|
                         (signed; +ve => early money backing that selection)
"""

from __future__ import annotations

import math
from dataclasses import dataclass, asdict, field
from typing import Dict, List, Optional, Tuple

from .schema import Horizon, MarketKey
from .timeseries import MarketTimeSeries


@dataclass
class EfficiencySignals:
    match_id: str
    market: str
    horizon_used: str
    n_books: int
    consensus_prob: Dict[str, float]               # selection -> fair prob
    disagreement: Dict[str, float]                 # selection -> stdev
    market_consensus_score: Optional[float]
    bookmaker_disagreement_index: Optional[float]
    sharp_proxy: Dict[str, float]                  # selection -> early-move score
    sharp_proxy_signal: Optional[float]
    sharp_proxy_selection: Optional[str]
    mean_overround: Optional[float]                # market efficiency (lower=tighter)

    def to_dict(self) -> dict:
        return asdict(self)


class MarketEfficiencyEngine:
    """Cross-book efficiency measurement. Pure / stateless."""

    def compute(
        self,
        per_book_series: Dict[MarketKey, MarketTimeSeries],
        consensus_horizon: Horizon = Horizon.H1,
    ) -> Dict[str, EfficiencySignals]:
        # group series by (match_id, market): { (m,mkt): { book: { sel: series }}}
        groups: Dict[Tuple[str, str], Dict[str, Dict[str, MarketTimeSeries]]] = {}
        for key, series in per_book_series.items():
            if key.bookmaker is None:
                continue
            g = groups.setdefault((key.match_id, key.market), {})
            g.setdefault(key.bookmaker, {})[key.selection] = series

        out: Dict[str, EfficiencySignals] = {}
        for (match_id, market), books in groups.items():
            out[f"{match_id}|{market}"] = self._compute_one(
                match_id, market, books, consensus_horizon
            )
        return out

    # -- internals ----------------------------------------------------------
    def _compute_one(self, match_id, market, books, consensus_horizon):
        # --- de-vig at the consensus horizon (fallback to latest available) -
        per_book_fair: Dict[str, Dict[str, float]] = {}
        overrounds: List[float] = []
        for bk, sels in books.items():
            odds = {}
            for sel, series in sels.items():
                o = self._odds_with_fallback(series, consensus_horizon)
                if o and o > 1.0:
                    odds[sel] = o
            fair, R = self._devig(odds)
            if fair:
                per_book_fair[bk] = fair
                overrounds.append(R)

        selections = sorted({s for f in per_book_fair.values() for s in f})
        consensus_prob: Dict[str, float] = {}
        disagreement: Dict[str, float] = {}
        for s in selections:
            vals = [per_book_fair[bk][s] for bk in per_book_fair if s in per_book_fair[bk]]
            if vals:
                consensus_prob[s] = sum(vals) / len(vals)
                disagreement[s] = self._stdev(vals)

        # market_consensus_score = 1 - mean coefficient of variation
        cvs = [
            disagreement[s] / consensus_prob[s]
            for s in selections
            if consensus_prob.get(s)
        ]
        consensus_score = self._clip(1.0 - (sum(cvs) / len(cvs)), 0.0, 1.0) if cvs else None
        disagreement_index = (
            sum(disagreement.values()) / len(disagreement) if disagreement else None
        )

        # --- sharp proxy: early (open -> 24h) confidence-weighted move ------
        sharp_proxy: Dict[str, float] = {}
        for s in selections:
            num = 0.0
            den = 0.0
            for bk, sels in books.items():
                series = sels.get(s)
                if not series:
                    continue
                p_open = self._fair_prob_at(books, bk, market, s, Horizon.OPENING)
                p_24 = self._fair_prob_at(books, bk, market, s, Horizon.H24)
                if p_open is None or p_24 is None:
                    continue
                conf = series.ordered[-1].confidence_score if series.ordered else 0.5
                num += conf * (p_24 - p_open)
                den += conf
            if den:
                sharp_proxy[s] = num / den

        sel_signal, sig_val = None, None
        if sharp_proxy:
            sel_signal = max(sharp_proxy, key=lambda s: abs(sharp_proxy[s]))
            sig_val = sharp_proxy[sel_signal]

        return EfficiencySignals(
            match_id=match_id,
            market=market,
            horizon_used=consensus_horizon.value,
            n_books=len(books),
            consensus_prob={k: round(v, 6) for k, v in consensus_prob.items()},
            disagreement={k: round(v, 6) for k, v in disagreement.items()},
            market_consensus_score=consensus_score,
            bookmaker_disagreement_index=disagreement_index,
            sharp_proxy={k: round(v, 6) for k, v in sharp_proxy.items()},
            sharp_proxy_signal=sig_val,
            sharp_proxy_selection=sel_signal,
            mean_overround=(sum(overrounds) / len(overrounds)) if overrounds else None,
        )

    # de-vig a single book's selection->odds dict
    @staticmethod
    def _devig(odds: Dict[str, float]) -> Tuple[Dict[str, float], float]:
        if not odds:
            return {}, 0.0
        raw = {s: 1.0 / o for s, o in odds.items()}
        R = sum(raw.values())
        if R <= 0:
            return {}, 0.0
        return {s: p / R for s, p in raw.items()}, R

    def _fair_prob_at(self, books, bk, market, sel, horizon) -> Optional[float]:
        """De-vigged prob for one book/selection at a horizon (needs all sels)."""
        sels = books.get(bk, {})
        odds = {}
        for s, series in sels.items():
            o = series.odds_at(horizon)
            if o and o > 1.0:
                odds[s] = o
        fair, _ = self._devig(odds)
        return fair.get(sel)

    @staticmethod
    def _odds_with_fallback(series: MarketTimeSeries, horizon: Horizon) -> Optional[float]:
        order = [horizon, Horizon.H1, Horizon.H6, Horizon.H12, Horizon.H24, Horizon.OPENING]
        for h in order:
            o = series.odds_at(h)
            if o:
                return o
        return None

    @staticmethod
    def _stdev(vals: List[float]) -> float:
        n = len(vals)
        if n < 2:
            return 0.0
        mean = sum(vals) / n
        return math.sqrt(sum((v - mean) ** 2 for v in vals) / (n - 1))

    @staticmethod
    def _clip(x, lo, hi):
        return max(lo, min(hi, x))
