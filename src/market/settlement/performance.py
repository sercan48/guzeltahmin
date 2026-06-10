"""M8.4 — Performance Finalization & Attribution Layer.

Consumes M8.3 finalized settlement metrics and aggregates them into deterministic,
replay-safe evaluation + attribution views: rolling analytics, league/market/
regime/tier segmentation, sharp-vs-soft attribution, and stability metrics.

Additive: reads M8.3 ``MetricRecord``s (enriched with attribution dimensions by
the caller). No M1-M8.3 changes, no ML/prediction/threshold/betting logic.
Void settlements are excluded from ROI/CLV aggregates (counted separately),
consistent with M8.3.
"""

from __future__ import annotations

import math
import sqlite3
from dataclasses import dataclass, asdict
from typing import Dict, List, Optional

# attribution dimensions the aggregator segments by
_SEGMENT_FIELDS = {"league", "market", "regime", "tier", "source_class"}
# stability normalization scales (stdev that maps to zero stability)
_CLV_REF, _ROI_REF, _CONF_REF = 0.05, 1.0, 0.30
_MIN_SAMPLE = 30


@dataclass
class PerformanceEntry:
    metric_id: str
    match_id: str
    league: str
    market: str
    selection: str
    regime: str
    tier: str
    source_class: str        # SHARP / SOFT / MIXED
    realized_roi: Optional[float]
    realized_clv: Optional[float]
    settlement_confidence: float
    is_void: bool
    status: str


@dataclass
class SegmentStats:
    segment: str
    n: int
    n_void: int
    roi_total: float
    roi_mean: Optional[float]
    clv_mean: Optional[float]
    conf_mean: Optional[float]
    beat_rate: Optional[float]
    conf_weighted_roi: Optional[float]
    conf_weighted_clv: Optional[float]

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class StabilityReport:
    n: int
    clv_stability: Optional[float]
    roi_stability: Optional[float]
    confidence_stability: Optional[float]
    sample_sufficient: bool

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class AttributionReport:
    sharp: SegmentStats
    soft: SegmentStats
    roi_delta: Optional[float]      # sharp - soft
    clv_delta: Optional[float]

    def to_dict(self) -> dict:
        return {"sharp": self.sharp.to_dict(), "soft": self.soft.to_dict(),
                "roi_delta": self.roi_delta, "clv_delta": self.clv_delta}


def _mean(xs: List[float]) -> Optional[float]:
    return round(sum(xs) / len(xs), 6) if xs else None


def _stdev(xs: List[float]) -> Optional[float]:
    if len(xs) < 2:
        return 0.0 if xs else None
    m = sum(xs) / len(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1))


def _wmean(pairs: List[tuple]) -> Optional[float]:
    wsum = sum(w for _, w in pairs)
    return round(sum(v * w for v, w in pairs) / wsum, 6) if wsum > 0 else None


def _stability(xs: List[float], ref: float) -> Optional[float]:
    s = _stdev(xs)
    if s is None:
        return None
    return round(max(0.0, 1.0 - min(1.0, s / ref)), 6)


class PerformanceAggregator:
    """Append-only performance ledger + deterministic aggregation/attribution."""

    def __init__(self, db_path: str = ":memory:") -> None:
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS performance_entries (
                seq INTEGER PRIMARY KEY AUTOINCREMENT,
                metric_id TEXT UNIQUE, match_id TEXT, league TEXT, market TEXT,
                selection TEXT, regime TEXT, tier TEXT, source_class TEXT,
                realized_roi REAL, realized_clv REAL, settlement_confidence REAL,
                is_void INTEGER, status TEXT
            );
            """
        )
        self.conn.commit()

    # -- ingest (append-only, idempotent) ----------------------------------
    def ingest(self, e: PerformanceEntry) -> bool:
        """Append an entry. Idempotent by metric_id; returns False on duplicate."""
        if self.conn.execute("SELECT 1 FROM performance_entries WHERE metric_id=?",
                             (e.metric_id,)).fetchone():
            return False
        self.conn.execute(
            "INSERT INTO performance_entries (metric_id,match_id,league,market,selection,"
            "regime,tier,source_class,realized_roi,realized_clv,settlement_confidence,"
            "is_void,status) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (e.metric_id, e.match_id, e.league, e.market, e.selection, e.regime, e.tier,
             e.source_class, e.realized_roi, e.realized_clv, e.settlement_confidence,
             1 if e.is_void else 0, e.status),
        )
        self.conn.commit()
        return True

    def ingest_from_metric(self, metric, *, league: str, regime: str, tier: str,
                           source_class: str) -> bool:
        """Build a PerformanceEntry from an M8.3 MetricRecord + attribution dims."""
        return self.ingest(PerformanceEntry(
            metric_id=metric.metric_id, match_id=metric.match_id, league=league,
            market=metric.market, selection=metric.selection, regime=regime, tier=tier,
            source_class=source_class, realized_roi=metric.realized_roi,
            realized_clv=metric.realized_clv,
            settlement_confidence=metric.settlement_confidence,
            is_void=metric.is_void, status=metric.status))

    # -- aggregation --------------------------------------------------------
    def global_summary(self) -> SegmentStats:
        return self._aggregate(self._rows(), "ALL")

    def segment(self, by: str) -> Dict[str, SegmentStats]:
        if by not in _SEGMENT_FIELDS:
            raise ValueError(f"unknown segment field: {by}")
        groups: Dict[str, List[sqlite3.Row]] = {}
        for r in self._rows():
            groups.setdefault(r[by], []).append(r)
        return {k: self._aggregate(v, k) for k, v in sorted(groups.items())}

    def sharp_vs_soft(self) -> AttributionReport:
        rows = self._rows()
        sharp = self._aggregate([r for r in rows if r["source_class"] == "SHARP"], "SHARP")
        soft = self._aggregate([r for r in rows if r["source_class"] == "SOFT"], "SOFT")
        roi_delta = (sharp.roi_mean - soft.roi_mean) \
            if (sharp.roi_mean is not None and soft.roi_mean is not None) else None
        clv_delta = (sharp.clv_mean - soft.clv_mean) \
            if (sharp.clv_mean is not None and soft.clv_mean is not None) else None
        return AttributionReport(sharp, soft, _round(roi_delta), _round(clv_delta))

    def rolling(self, window: int = 100) -> SegmentStats:
        rows = self.conn.execute(
            "SELECT * FROM performance_entries ORDER BY seq DESC LIMIT ?", (window,)
        ).fetchall()
        return self._aggregate(rows, f"rolling_{window}")

    def stability(self) -> StabilityReport:
        rows = [r for r in self._rows() if not r["is_void"]]
        roi = [r["realized_roi"] for r in rows if r["realized_roi"] is not None]
        clv = [r["realized_clv"] for r in rows if r["realized_clv"] is not None]
        conf = [r["settlement_confidence"] for r in self._rows()]
        return StabilityReport(
            n=len(rows),
            clv_stability=_stability(clv, _CLV_REF),
            roi_stability=_stability(roi, _ROI_REF),
            confidence_stability=_stability(conf, _CONF_REF),
            sample_sufficient=len(rows) >= _MIN_SAMPLE,
        )

    # -- replay -------------------------------------------------------------
    def replay(self) -> dict:
        """Deterministic reconstruction of the headline views."""
        return {
            "global": self.global_summary().to_dict(),
            "by_league": {k: v.to_dict() for k, v in self.segment("league").items()},
            "by_market": {k: v.to_dict() for k, v in self.segment("market").items()},
            "by_regime": {k: v.to_dict() for k, v in self.segment("regime").items()},
            "by_tier": {k: v.to_dict() for k, v in self.segment("tier").items()},
            "sharp_vs_soft": self.sharp_vs_soft().to_dict(),
            "stability": self.stability().to_dict(),
        }

    # -- internals ----------------------------------------------------------
    def _rows(self) -> List[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM performance_entries ORDER BY seq").fetchall()

    @staticmethod
    def _aggregate(rows: List[sqlite3.Row], label: str) -> SegmentStats:
        n = len(rows)
        n_void = sum(1 for r in rows if r["is_void"])
        live = [r for r in rows if not r["is_void"]]
        roi = [r["realized_roi"] for r in live if r["realized_roi"] is not None]
        clv = [r["realized_clv"] for r in live if r["realized_clv"] is not None]
        conf = [r["settlement_confidence"] for r in rows]
        roi_pairs = [(r["realized_roi"], r["settlement_confidence"]) for r in live
                     if r["realized_roi"] is not None]
        clv_pairs = [(r["realized_clv"], r["settlement_confidence"]) for r in live
                     if r["realized_clv"] is not None]
        beat = ([1 for v in clv if v > 0])
        return SegmentStats(
            segment=label, n=n, n_void=n_void,
            roi_total=round(sum(roi), 6),
            roi_mean=_mean(roi), clv_mean=_mean(clv), conf_mean=_mean(conf),
            beat_rate=round(len(beat) / len(clv), 6) if clv else None,
            conf_weighted_roi=_wmean(roi_pairs), conf_weighted_clv=_wmean(clv_pairs),
        )

    def close(self) -> None:
        self.conn.close()


def _round(x):
    return None if x is None else round(x, 6)
