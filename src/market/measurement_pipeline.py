"""Measurement-layer orchestrator (R1.2).

Wires the five tasks into one deterministic pass over a snapshot stream:

    raw OddsRecord[]  ──▶  MarketTimeSeriesBuilder (per-book + consensus)
                            │
            ┌───────────────┼───────────────┬───────────────┐
            ▼               ▼               ▼               ▼
       OddsDrift       CLVFoundation   Efficiency      Integrity
       (consensus)     (consensus +    (cross-book)    (per-book)
                        per-book adj)

Consensus stream construction (point-in-time, no leakage)
---------------------------------------------------------
For each (match, market) and each timestamp t present in any book's feed, take
every book's latest quote at/before t for all selections, de-vig per book, then
average the fair probabilities across books → consensus fair prob per
selection. Consensus "fair odds" = 1/prob. This yields a leakage-free consensus
OddsRecord stream that drift/CLV consume.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional

from .schema import OddsRecord, MarketKey, MatchContext, SnapshotType
from .timeseries import MarketTimeSeriesBuilder, MarketTimeSeries
from .drift_engine import OddsDriftEngine, DriftSignals
from .clv_foundation import CLVFoundation, CLVResult, BookmakerAdjustedCLV
from .efficiency_signals import MarketEfficiencyEngine, EfficiencySignals
from .integrity import DataIntegrityLayer, IntegrityReport
from .schema import Horizon


@dataclass
class MeasurementResult:
    drift: Dict[str, DriftSignals]
    clv_consensus: Dict[str, CLVResult]
    clv_bookmaker_adjusted: Dict[str, BookmakerAdjustedCLV]
    efficiency: Dict[str, EfficiencySignals]
    integrity: IntegrityReport
    n_records: int
    n_book_series: int
    n_consensus_series: int

    def to_dict(self) -> dict:
        return {
            "n_records": self.n_records,
            "n_book_series": self.n_book_series,
            "n_consensus_series": self.n_consensus_series,
            "drift": {k: v.to_dict() for k, v in self.drift.items()},
            "clv_consensus": {k: v.to_dict() for k, v in self.clv_consensus.items()},
            "clv_bookmaker_adjusted": {
                k: v.to_dict() for k, v in self.clv_bookmaker_adjusted.items()
            },
            "efficiency": {k: v.to_dict() for k, v in self.efficiency.items()},
            "integrity": self.integrity.to_dict(),
        }


class MeasurementPipeline:
    def __init__(self) -> None:
        self.builder = MarketTimeSeriesBuilder(dedup=True)
        self.drift = OddsDriftEngine()
        self.clv = CLVFoundation()
        self.efficiency = MarketEfficiencyEngine()
        self.integrity = DataIntegrityLayer()

    def run(
        self,
        records: List[OddsRecord],
        contexts: Dict[str, MatchContext],
        consensus_horizon: Horizon = Horizon.H1,
    ) -> MeasurementResult:
        # 1. per-book series (bookmaker set on the key)
        book_series = self.builder.build(records, contexts)

        # 2. consensus stream -> consensus series
        consensus_records = self._build_consensus_records(records, contexts)
        consensus_series = self.builder.build(consensus_records, contexts)

        # 3. signals
        drift = self.drift.compute_all(consensus_series)
        clv_consensus = self.clv.compute_all(consensus_series)
        clv_adj = self.clv.bookmaker_adjusted(book_series)
        eff = self.efficiency.compute(book_series, consensus_horizon)
        integ = self.integrity.validate(book_series)

        return MeasurementResult(
            drift=drift,
            clv_consensus=clv_consensus,
            clv_bookmaker_adjusted=clv_adj,
            efficiency=eff,
            integrity=integ,
            n_records=len(records),
            n_book_series=len(book_series),
            n_consensus_series=len(consensus_series),
        )

    # -- consensus construction --------------------------------------------
    def _build_consensus_records(
        self, records: List[OddsRecord], contexts: Dict[str, MatchContext]
    ) -> List[OddsRecord]:
        # index: (match, market) -> book -> sel -> sorted [(ts, odds, conf)]
        idx: Dict[tuple, Dict[str, Dict[str, List[tuple]]]] = {}
        for r in records:
            if r.match_id not in contexts or r.odds <= 1.0:
                continue
            mk = (r.match_id, r.market)
            idx.setdefault(mk, {}).setdefault(r.bookmaker, {}).setdefault(
                r.selection, []
            ).append((r.timestamp, r.odds, r.confidence_score))

        out: List[OddsRecord] = []
        for (match_id, market), books in idx.items():
            # universe of timestamps across all books/selections
            all_ts = sorted({
                ts
                for sels in books.values()
                for arr in sels.values()
                for (ts, _, _) in arr
            })
            # pre-sort each book/sel series
            for sels in books.values():
                for arr in sels.values():
                    arr.sort(key=lambda x: x[0])

            selections = sorted({
                s for sels in books.values() for s in sels.keys()
            })
            for t in all_ts:
                # per book, latest quote at/before t for every selection -> de-vig
                fair_by_sel: Dict[str, List[float]] = {s: [] for s in selections}
                confs: List[float] = []
                for bk, sels in books.items():
                    book_odds = {}
                    for s in selections:
                        q = self._latest_le(sels.get(s, []), t)
                        if q is not None:
                            book_odds[s] = q[1]
                    # need full market to de-vig
                    if len(book_odds) < len(selections):
                        continue
                    raw = {s: 1.0 / o for s, o in book_odds.items()}
                    R = sum(raw.values())
                    if R <= 0:
                        continue
                    for s in selections:
                        fair_by_sel[s].append(raw[s] / R)
                    # confidence at t for this book (use any selection's)
                    any_sel = next(iter(sels.values()))
                    qc = self._latest_le(any_sel, t)
                    confs.append(qc[2] if qc else 0.5)

                if not confs:
                    continue
                mean_conf = sum(confs) / len(confs)
                for s in selections:
                    vals = fair_by_sel[s]
                    if not vals:
                        continue
                    cons_p = sum(vals) / len(vals)
                    if cons_p <= 0:
                        continue
                    out.append(OddsRecord(
                        match_id=match_id,
                        bookmaker="consensus",
                        market=market,
                        selection=s,
                        odds=1.0 / cons_p,           # fair consensus odds
                        timestamp=t,
                        snapshot_type=SnapshotType.OPEN.value,
                        source_id="consensus_synth",
                        confidence_score=mean_conf,
                    ))
        return out

    @staticmethod
    def _latest_le(arr: List[tuple], t: datetime) -> Optional[tuple]:
        chosen = None
        for item in arr:  # arr sorted ascending by ts
            if item[0] <= t:
                chosen = item
            else:
                break
        return chosen
