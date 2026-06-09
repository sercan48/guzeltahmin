"""Task 3 — CLV Foundation Layer.

NO model predictions are used here. This layer measures *closing line value*
purely from observed odds. Closing odds are a **future placeholder** (R2/R3
scheduler populates the real close); until then CLV is reported as PENDING, or
computed against a clearly-flagged *provisional* close (the last pre-kickoff
snapshot).

Core definition (as specified)
------------------------------
    CLV_raw = (closing_odds - entry_odds) / entry_odds

This is the raw fractional line move from an entry price to the close. Note the
sign: CLV_raw > 0 means the line *lengthened* after entry (closing odds higher
than entry). For a backer the conventional "beat the close" value is the
inverse move, so we additionally expose:

    clv_backer = (entry_odds / closing_odds) - 1     # +ve => beat the close

both are reported; CLV_raw is the canonical field requested.

Snapshot-level CLV
------------------
CLV_raw computed from each horizon's entry price against the same close:
    CLV_raw[h] = (closing - odds[h]) / odds[h]   for h in {open,24h,12h,6h,1h}

Time-decay weighted CLV
-----------------------
Weights emphasise entries closer to the close (more informative):
    w_h = exp(-lambda * dh)        dh = hours between entry h and the close
    weighted_CLV = sum(w_h * CLV_raw[h]) / sum(w_h)
lambda defaults to ln(2)/12  => 12-hour half-life.

Bookmaker-adjusted CLV
----------------------
When multiple sources exist for the same (match, market, selection), CLV is
computed per bookmaker against that bookmaker's own close, then aggregated
weighted by confidence_score. Cross-book dispersion is also reported.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, asdict, field
from typing import Dict, List, Optional

from .schema import Horizon, MarketKey
from .timeseries import MarketTimeSeries

_LAMBDA_12H = math.log(2) / 12.0  # 12h half-life decay


@dataclass
class CLVResult:
    key_str: str
    status: str                              # COMPUTED | PROVISIONAL | PENDING_CLOSE
    closing_odds: Optional[float]
    entry_reference: Optional[str]           # which horizon was the primary entry
    entry_odds: Optional[float]
    clv_raw: Optional[float]                 # (close - entry)/entry  [canonical]
    clv_backer: Optional[float]              # (entry/close)-1  [interpretation aid]
    clv_by_snapshot: Dict[str, Optional[float]] = field(default_factory=dict)
    weighted_clv: Optional[float] = None
    provisional_close: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class BookmakerAdjustedCLV:
    match_id: str
    market: str
    selection: str
    per_bookmaker: Dict[str, Optional[float]]   # bookmaker -> CLV_raw
    confidence_weighted_clv: Optional[float]
    clv_dispersion: Optional[float]             # stdev across books
    n_books: int

    def to_dict(self) -> dict:
        return asdict(self)


class CLVFoundation:
    """Pure CLV measurement. No ML."""

    def __init__(self, decay_lambda: float = _LAMBDA_12H) -> None:
        self.decay_lambda = decay_lambda

    # -- single series ------------------------------------------------------
    def compute(self, series: MarketTimeSeries) -> CLVResult:
        close_pt = series.point(Horizon.CLOSING)
        closing = close_pt.odds if close_pt else None
        key_str = self._key_str(series.key)

        if closing is None:
            return CLVResult(
                key_str=key_str,
                status="PENDING_CLOSE",
                closing_odds=None,
                entry_reference=None,
                entry_odds=None,
                clv_raw=None,
                clv_backer=None,
            )

        # snapshot-level CLV for every available pre-close horizon
        clv_by_snapshot: Dict[str, Optional[float]] = {}
        decay_terms: List[float] = []
        weight_terms: List[float] = []
        ko_close_age = close_pt.actual_age_hours or 0.0

        for h in (Horizon.OPENING, Horizon.H24, Horizon.H12, Horizon.H6, Horizon.H1):
            pt = series.point(h)
            entry = pt.odds if pt else None
            if entry is None or entry <= 1.0:
                clv_by_snapshot[h.value] = None
                continue
            clv = (closing - entry) / entry
            clv_by_snapshot[h.value] = clv
            # dh = hours between this entry and the close
            entry_age = pt.actual_age_hours if pt.actual_age_hours is not None else 0.0
            dh = max(0.0, entry_age - ko_close_age)
            w = math.exp(-self.decay_lambda * dh)
            decay_terms.append(w * clv)
            weight_terms.append(w)

        weighted = (sum(decay_terms) / sum(weight_terms)) if weight_terms else None

        # primary entry = the most recent pre-close horizon that exists
        entry_ref, entry_odds = self._primary_entry(series)
        clv_raw = ((closing - entry_odds) / entry_odds) if entry_odds else None
        clv_backer = ((entry_odds / closing) - 1.0) if entry_odds else None

        return CLVResult(
            key_str=key_str,
            status="PROVISIONAL" if series.kickoff_provisional_close else "COMPUTED",
            closing_odds=closing,
            entry_reference=entry_ref.value if entry_ref else None,
            entry_odds=entry_odds,
            clv_raw=clv_raw,
            clv_backer=clv_backer,
            clv_by_snapshot=clv_by_snapshot,
            weighted_clv=weighted,
            provisional_close=series.kickoff_provisional_close,
        )

    def compute_all(self, series_map) -> Dict[str, CLVResult]:
        return {self._key_str(s.key): self.compute(s) for s in series_map.values()}

    # -- bookmaker-adjusted -------------------------------------------------
    def bookmaker_adjusted(
        self, per_book_series: Dict[MarketKey, MarketTimeSeries]
    ) -> Dict[str, BookmakerAdjustedCLV]:
        """Aggregate CLV across single-book series sharing (match, market, sel).

        ``per_book_series`` must be keyed by MarketKey *with* bookmaker set.
        """
        groups: Dict[tuple, Dict[str, MarketTimeSeries]] = {}
        for key, series in per_book_series.items():
            if key.bookmaker is None:
                continue
            groups.setdefault((key.match_id, key.market, key.selection), {})[
                key.bookmaker
            ] = series

        out: Dict[str, BookmakerAdjustedCLV] = {}
        for (match_id, market, selection), books in groups.items():
            per_book: Dict[str, Optional[float]] = {}
            weighted_num = 0.0
            weighted_den = 0.0
            vals: List[float] = []
            for bk, series in books.items():
                res = self.compute(series)
                per_book[bk] = res.clv_raw
                if res.clv_raw is not None:
                    conf = self._latest_confidence(series)
                    weighted_num += conf * res.clv_raw
                    weighted_den += conf
                    vals.append(res.clv_raw)
            wclv = (weighted_num / weighted_den) if weighted_den else None
            dispersion = self._stdev(vals) if len(vals) >= 2 else None
            label = f"{match_id}|{market}|{selection}"
            out[label] = BookmakerAdjustedCLV(
                match_id=match_id,
                market=market,
                selection=selection,
                per_bookmaker=per_book,
                confidence_weighted_clv=wclv,
                clv_dispersion=dispersion,
                n_books=len(books),
            )
        return out

    # -- helpers ------------------------------------------------------------
    @staticmethod
    def _primary_entry(series: MarketTimeSeries):
        for h in (Horizon.H1, Horizon.H6, Horizon.H12, Horizon.H24, Horizon.OPENING):
            pt = series.point(h)
            if pt and pt.odds:
                return h, pt.odds
        return None, None

    @staticmethod
    def _latest_confidence(series: MarketTimeSeries) -> float:
        return series.ordered[-1].confidence_score if series.ordered else 0.5

    @staticmethod
    def _stdev(vals: List[float]) -> float:
        n = len(vals)
        mean = sum(vals) / n
        var = sum((v - mean) ** 2 for v in vals) / (n - 1)
        return math.sqrt(var)

    @staticmethod
    def _key_str(key: MarketKey) -> str:
        bk = f"@{key.bookmaker}" if key.bookmaker else "@consensus"
        return f"{key.match_id}|{key.market}|{key.selection}{bk}"
