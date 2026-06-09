"""Task 1 — Market Time-Series Builder.

Transforms an unordered stream of ``OddsRecord`` into time-aligned horizon
buckets per ``(match_id, market, selection[, bookmaker])``:

    opening / 24h / 12h / 6h / 1h / live / closing

Guarantees
----------
1. Timestamp alignment per MarketKey — every bucket is the point-in-time line
   as it stood at that horizon.
2. No leakage — a horizon bucket NEVER uses a snapshot whose timestamp is
   after the horizon's cut-off (= kickoff - hours_before_ko). The 24h bucket
   only ever sees data available 24h before kickoff.
3. Strict ordering — within a series records are sorted ascending by
   timestamp; ties are broken deterministically and exact duplicates collapsed
   (highest confidence kept).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Dict, List, Optional, Sequence

from .schema import (
    Horizon,
    OddsRecord,
    MarketKey,
    MatchContext,
    PREMATCH_HORIZONS,
)


@dataclass(frozen=True)
class HorizonPoint:
    """A resolved horizon bucket for one MarketKey."""

    horizon: Horizon
    record: Optional[OddsRecord]          # None when no snapshot satisfies it
    target_age_hours: Optional[float]     # intended hours-before-KO
    actual_age_hours: Optional[float]     # real age of the chosen snapshot
    gap_hours: Optional[float]            # |actual - target|; staleness of bucket

    @property
    def odds(self) -> Optional[float]:
        return self.record.odds if self.record else None

    @property
    def implied_prob(self) -> Optional[float]:
        return self.record.implied_prob if self.record else None


@dataclass
class MarketTimeSeries:
    """Ordered series + resolved horizon buckets for one MarketKey."""

    key: MarketKey
    ordered: List[OddsRecord]                 # strict ascending by timestamp
    horizons: Dict[Horizon, HorizonPoint]
    kickoff_provisional_close: bool           # True => closing is a placeholder

    def point(self, h: Horizon) -> Optional[HorizonPoint]:
        return self.horizons.get(h)

    def odds_at(self, h: Horizon) -> Optional[float]:
        p = self.horizons.get(h)
        return p.odds if p else None


class MarketTimeSeriesBuilder:
    """Builds time-aligned series from raw snapshots.

    Parameters
    ----------
    dedup : bool
        Collapse records sharing the same (timestamp) within a MarketKey,
        keeping the highest-confidence one (defends against R1.1 leaving a
        residual duplicate).
    """

    def __init__(self, dedup: bool = True) -> None:
        self.dedup = dedup

    # -- public -------------------------------------------------------------
    def build(
        self,
        records: Sequence[OddsRecord],
        contexts: Dict[str, MatchContext],
    ) -> Dict[MarketKey, MarketTimeSeries]:
        """Group, order and horizon-align every MarketKey present in ``records``.

        ``contexts`` maps match_id -> MatchContext (kickoff is mandatory).
        Records whose match_id has no context are skipped (cannot be anchored).
        """
        grouped: Dict[MarketKey, List[OddsRecord]] = {}
        for r in records:
            if r.match_id not in contexts:
                continue
            grouped.setdefault(r.key, []).append(r)

        out: Dict[MarketKey, MarketTimeSeries] = {}
        for key, recs in grouped.items():
            ctx = contexts[key.match_id]
            ordered = self._order(recs)
            horizons, provisional = self._resolve_horizons(ordered, ctx)
            out[key] = MarketTimeSeries(
                key=key,
                ordered=ordered,
                horizons=horizons,
                kickoff_provisional_close=provisional,
            )
        return out

    # -- internals ----------------------------------------------------------
    def _order(self, recs: List[OddsRecord]) -> List[OddsRecord]:
        """Strict chronological ordering with deterministic dedup."""
        # Stable sort: timestamp, then descending confidence so the keeper is
        # first among same-timestamp records.
        ordered = sorted(recs, key=lambda r: (r.timestamp, -r.confidence_score))
        if not self.dedup:
            return ordered
        deduped: List[OddsRecord] = []
        seen_ts = set()
        for r in ordered:
            stamp = r.timestamp
            if stamp in seen_ts:
                continue  # keep first (highest confidence) at this timestamp
            seen_ts.add(stamp)
            deduped.append(r)
        return deduped

    def _resolve_horizons(
        self, ordered: List[OddsRecord], ctx: MatchContext
    ):
        ko = ctx.kickoff
        horizons: Dict[Horizon, HorizonPoint] = {}

        # --- opening: earliest snapshot overall ---------------------------
        opening = ordered[0] if ordered else None
        horizons[Horizon.OPENING] = HorizonPoint(
            horizon=Horizon.OPENING,
            record=opening,
            target_age_hours=None,
            actual_age_hours=self._age_h(opening, ko),
            gap_hours=None,
        )

        # --- fixed pre-match anchors (24/12/6/1h) -------------------------
        for h in PREMATCH_HORIZONS:
            target = h.hours_before_ko
            cutoff = ko - timedelta(hours=target)
            # leakage-free: latest record at or before the cutoff
            chosen = self._latest_at_or_before(ordered, cutoff)
            actual_age = self._age_h(chosen, ko)
            gap = abs(actual_age - target) if actual_age is not None else None
            horizons[h] = HorizonPoint(
                horizon=h,
                record=chosen,
                target_age_hours=target,
                actual_age_hours=actual_age,
                gap_hours=gap,
            )

        # --- live: earliest snapshot strictly after kickoff ---------------
        live = next((r for r in ordered if r.timestamp > ko), None)
        horizons[Horizon.LIVE] = HorizonPoint(
            horizon=Horizon.LIVE,
            record=live,
            target_age_hours=None,
            actual_age_hours=self._age_h(live, ko),
            gap_hours=None,
        )

        # --- closing: explicit close, else last pre-KO (provisional) ------
        provisional = False
        close_rec: Optional[OddsRecord] = None
        if ctx.closing_ts is not None:
            close_rec = self._record_at(ordered, ctx.closing_ts)
        if close_rec is None:
            explicit = [r for r in ordered if r.snapshot_type == "CLOSE"]
            close_rec = explicit[-1] if explicit else None
        if close_rec is None:
            # provisional close = last snapshot at or before kickoff
            close_rec = self._latest_at_or_before(ordered, ko)
            provisional = close_rec is not None
        horizons[Horizon.CLOSING] = HorizonPoint(
            horizon=Horizon.CLOSING,
            record=close_rec,
            target_age_hours=0.0,
            actual_age_hours=self._age_h(close_rec, ko),
            gap_hours=None,
        )
        return horizons, provisional

    @staticmethod
    def _latest_at_or_before(ordered: List[OddsRecord], cutoff) -> Optional[OddsRecord]:
        chosen = None
        for r in ordered:  # ordered ascending
            if r.timestamp <= cutoff:
                chosen = r
            else:
                break
        return chosen

    @staticmethod
    def _record_at(ordered: List[OddsRecord], ts) -> Optional[OddsRecord]:
        for r in ordered:
            if r.timestamp == ts:
                return r
        return None

    @staticmethod
    def _age_h(rec: Optional[OddsRecord], ko) -> Optional[float]:
        if rec is None:
            return None
        return (ko - rec.timestamp).total_seconds() / 3600.0
