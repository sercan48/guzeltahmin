"""M8.5 — Signal-to-Outcome Integration & Final Grading Layer.

Connects M5 PaperSignal outputs with the M8.1-M8.4 settlement/performance system,
closing the loop: a signal is *registered* at emission (immutable) and later
*graded* against its settled outcome + locked close (via M8.3), then aggregated
into portfolio / tier / lifecycle / calibration views.

No-leakage by construction: emission-time fields (edge, predicted CLV, entry
odds, p_model) are written once at register() and never altered by grading; a
signal is graded only against ITS OWN match, and re-grading is idempotent — a
late outcome change cannot retroactively rewrite a finalized grade.

Additive: reads M5 PaperSignal + M8.1/M8.2/M8.3 ledgers. No M1-M8.4 changes, no
ML/prediction/threshold/betting logic.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from enum import Enum
from typing import Dict, List, Optional

from .math_engine import SettlementMathEngine, MetricStatus


class HitMiss(str, Enum):
    HIT = "HIT"
    MISS = "MISS"
    VOID = "VOID"
    PENDING = "PENDING"


def _sign(x: float) -> int:
    return (x > 0) - (x < 0)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class SignalInput:
    """Emission-time record of an M5 signal, enriched with grading inputs."""
    signal_id: str
    match_id: str
    market: str
    selection: str
    tier: str
    edge_score: float                # truth-adjusted edge at emission (predicted)
    entry_odds: float
    truth_confidence: float
    predicted_clv: Optional[float] = None
    p_model: Optional[float] = None
    emitted_state: str = "ACTIVE"
    emitted_at: str = ""
    league: str = "UNKNOWN"
    regime: str = "UNKNOWN"
    source_class: str = "MIXED"      # SHARP / SOFT / MIXED

    @classmethod
    def from_paper(cls, paper, *, signal_id: str, entry_odds: float,
                   predicted_clv: Optional[float] = None, p_model: Optional[float] = None,
                   emitted_state: str = "ACTIVE", league: str = "UNKNOWN",
                   regime: str = "UNKNOWN", source_class: str = "MIXED") -> "SignalInput":
        return cls(
            signal_id=signal_id, match_id=paper.match_id, market=paper.market,
            selection=paper.selection, tier=paper.tier, edge_score=paper.edge_score,
            entry_odds=entry_odds, truth_confidence=paper.truth_confidence,
            predicted_clv=predicted_clv, p_model=p_model, emitted_state=emitted_state,
            emitted_at=paper.timestamp, league=league, regime=regime,
            source_class=source_class)


@dataclass
class GradedSignal:
    signal_id: str
    match_id: str
    market: str
    selection: str
    tier: str
    emitted_state: str
    edge_score: float
    predicted_clv: Optional[float]
    entry_odds: float
    p_model: Optional[float]
    status: str                      # COMPLETED / VOID / PENDING / ...
    result: Optional[str]
    realized_roi: Optional[float]
    realized_clv: Optional[float]
    clv_error: Optional[float]       # realized - predicted
    clv_sign_agree: Optional[int]
    hit_miss: str
    calibration_error: Optional[float]
    truth_error: Optional[float]
    execution_error: Optional[float]

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class GroupSummary:
    label: str
    n: int
    n_void: int
    n_hit: int
    hit_rate: Optional[float]
    roi_total: float
    roi_mean: Optional[float]
    realized_clv_mean: Optional[float]
    mean_clv_error: Optional[float]
    clv_sign_agree_rate: Optional[float]

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class CalibrationReport:
    n: int
    mean_calibration_error: Optional[float]
    mean_truth_error: Optional[float]
    mean_execution_error: Optional[float]
    mean_abs_calibration_error: Optional[float]

    def to_dict(self) -> dict:
        return asdict(self)


def _mean(xs):
    return round(sum(xs) / len(xs), 6) if xs else None


class SignalGradingEngine:
    """Registers signals (immutable) and grades them against settled outcomes."""

    def __init__(self, db_path: str = ":memory:") -> None:
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS registered_signals (
                signal_id TEXT PRIMARY KEY, match_id TEXT, market TEXT, selection TEXT,
                tier TEXT, edge_score REAL, entry_odds REAL, truth_confidence REAL,
                predicted_clv REAL, p_model REAL, emitted_state TEXT, emitted_at TEXT,
                league TEXT, regime TEXT, source_class TEXT
            );
            CREATE TABLE IF NOT EXISTS signal_grades (
                signal_id TEXT PRIMARY KEY, status TEXT, result TEXT,
                realized_roi REAL, realized_clv REAL, clv_error REAL, clv_sign_agree INTEGER,
                hit_miss TEXT, calibration_error REAL, truth_error REAL, execution_error REAL,
                graded_at TEXT
            );
            """
        )
        self.conn.commit()

    # -- register (immutable emission record) ------------------------------
    def register(self, sig: SignalInput) -> bool:
        """Append the emission record. Idempotent by signal_id (immutable)."""
        if self.conn.execute("SELECT 1 FROM registered_signals WHERE signal_id=?",
                             (sig.signal_id,)).fetchone():
            return False
        self.conn.execute(
            "INSERT INTO registered_signals VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (sig.signal_id, sig.match_id, sig.market, sig.selection, sig.tier,
             sig.edge_score, sig.entry_odds, sig.truth_confidence, sig.predicted_clv,
             sig.p_model, sig.emitted_state, sig.emitted_at, sig.league, sig.regime,
             sig.source_class),
        )
        self.conn.commit()
        return True

    # -- grade against settled outcome + locked close ----------------------
    def grade(self, signal_id: str, settlement_ledger, closure_ledger,
              math_engine: SettlementMathEngine) -> Optional[GradedSignal]:
        """Grade a registered signal. Idempotent: a finalized grade is immutable;
        PENDING (no outcome yet) is not persisted so it can be graded later."""
        reg = self.conn.execute(
            "SELECT * FROM registered_signals WHERE signal_id=?", (signal_id,)
        ).fetchone()
        if reg is None:
            return None
        existing = self.conn.execute(
            "SELECT * FROM signal_grades WHERE signal_id=?", (signal_id,)
        ).fetchone()
        if existing:
            return self._join(reg, existing)        # immutable: never re-grade

        metric = math_engine.finalize_from_ledgers(
            settlement_ledger, closure_ledger, bet_id=signal_id,
            match_id=reg["match_id"], market=reg["market"], selection=reg["selection"],
            entry_odds=reg["entry_odds"], p_model=reg["p_model"],
            truth_conf=reg["truth_confidence"])

        if metric.status == MetricStatus.MISSING_OUTCOME.value:
            return self._join(reg, None, pending=True)   # not persisted

        predicted_clv = reg["predicted_clv"]
        clv_error = (metric.realized_clv - predicted_clv) \
            if (metric.realized_clv is not None and predicted_clv is not None) else None
        clv_sign = (1 if _sign(metric.realized_clv) == _sign(predicted_clv) else 0) \
            if (metric.realized_clv is not None and predicted_clv is not None) else None
        hit = (HitMiss.VOID if metric.is_void else
               (HitMiss.HIT if metric.result == "WON" else
                HitMiss.MISS if metric.result == "LOST" else HitMiss.PENDING))

        self.conn.execute(
            "INSERT INTO signal_grades VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (signal_id, metric.status, metric.result, metric.realized_roi,
             metric.realized_clv, None if clv_error is None else round(clv_error, 6),
             clv_sign, hit.value, metric.calibration_error, metric.truth_error,
             metric.execution_error, _now_iso()),
        )
        self.conn.commit()
        return self._join(reg, self.conn.execute(
            "SELECT * FROM signal_grades WHERE signal_id=?", (signal_id,)).fetchone())

    def get_grade(self, signal_id: str) -> Optional[GradedSignal]:
        reg = self.conn.execute("SELECT * FROM registered_signals WHERE signal_id=?",
                                (signal_id,)).fetchone()
        if reg is None:
            return None
        g = self.conn.execute("SELECT * FROM signal_grades WHERE signal_id=?",
                              (signal_id,)).fetchone()
        return self._join(reg, g, pending=g is None)

    # -- aggregation / reporting -------------------------------------------
    def portfolio_summary(self) -> GroupSummary:
        return self._summary(self._graded_rows(), "PORTFOLIO")

    def by_tier(self) -> Dict[str, GroupSummary]:
        return self._group_by("tier")

    def lifecycle_report(self) -> Dict[str, GroupSummary]:
        return self._group_by("emitted_state")

    def calibration_report(self) -> CalibrationReport:
        rows = [r for r in self._graded_rows() if r["calibration_error"] is not None]
        return CalibrationReport(
            n=len(rows),
            mean_calibration_error=_mean([r["calibration_error"] for r in rows]),
            mean_truth_error=_mean([r["truth_error"] for r in rows]),
            mean_execution_error=_mean([r["execution_error"] for r in rows]),
            mean_abs_calibration_error=_mean([abs(r["calibration_error"]) for r in rows]),
        )

    def replay(self) -> dict:
        return {
            "portfolio": self.portfolio_summary().to_dict(),
            "by_tier": {k: v.to_dict() for k, v in self.by_tier().items()},
            "lifecycle": {k: v.to_dict() for k, v in self.lifecycle_report().items()},
            "calibration": self.calibration_report().to_dict(),
        }

    # -- internals ----------------------------------------------------------
    def _graded_rows(self) -> List[sqlite3.Row]:
        return self.conn.execute(
            "SELECT r.*, g.status AS g_status, g.result, g.realized_roi, g.realized_clv, "
            "g.clv_error, g.clv_sign_agree, g.hit_miss, g.calibration_error, g.truth_error, "
            "g.execution_error FROM registered_signals r JOIN signal_grades g "
            "ON r.signal_id=g.signal_id ORDER BY r.signal_id"
        ).fetchall()

    def _group_by(self, field: str) -> Dict[str, GroupSummary]:
        groups: Dict[str, List[sqlite3.Row]] = {}
        for r in self._graded_rows():
            groups.setdefault(r[field], []).append(r)
        return {k: self._summary(v, k) for k, v in sorted(groups.items())}

    @staticmethod
    def _summary(rows: List[sqlite3.Row], label: str) -> GroupSummary:
        n = len(rows)
        n_void = sum(1 for r in rows if r["hit_miss"] == HitMiss.VOID.value)
        live = [r for r in rows if r["hit_miss"] in (HitMiss.HIT.value, HitMiss.MISS.value)]
        n_hit = sum(1 for r in rows if r["hit_miss"] == HitMiss.HIT.value)
        roi = [r["realized_roi"] for r in rows if not r["hit_miss"] == HitMiss.VOID.value
               and r["realized_roi"] is not None]
        clv = [r["realized_clv"] for r in rows if not r["hit_miss"] == HitMiss.VOID.value
               and r["realized_clv"] is not None]
        clv_err = [r["clv_error"] for r in rows if r["clv_error"] is not None]
        signs = [r["clv_sign_agree"] for r in rows if r["clv_sign_agree"] is not None]
        return GroupSummary(
            label=label, n=n, n_void=n_void, n_hit=n_hit,
            hit_rate=round(n_hit / len(live), 6) if live else None,
            roi_total=round(sum(roi), 6),
            roi_mean=_mean(roi), realized_clv_mean=_mean(clv),
            mean_clv_error=_mean(clv_err),
            clv_sign_agree_rate=round(sum(signs) / len(signs), 6) if signs else None,
        )

    @staticmethod
    def _join(reg: sqlite3.Row, g: Optional[sqlite3.Row], pending: bool = False) -> GradedSignal:
        return GradedSignal(
            signal_id=reg["signal_id"], match_id=reg["match_id"], market=reg["market"],
            selection=reg["selection"], tier=reg["tier"], emitted_state=reg["emitted_state"],
            edge_score=reg["edge_score"], predicted_clv=reg["predicted_clv"],
            entry_odds=reg["entry_odds"], p_model=reg["p_model"],
            status=(MetricStatus.MISSING_OUTCOME.value if (pending or g is None) else g["status"]),
            result=None if g is None else g["result"],
            realized_roi=None if g is None else g["realized_roi"],
            realized_clv=None if g is None else g["realized_clv"],
            clv_error=None if g is None else g["clv_error"],
            clv_sign_agree=None if g is None else g["clv_sign_agree"],
            hit_miss=HitMiss.PENDING.value if g is None else g["hit_miss"],
            calibration_error=None if g is None else g["calibration_error"],
            truth_error=None if g is None else g["truth_error"],
            execution_error=None if g is None else g["execution_error"],
        )

    def close(self) -> None:
        self.conn.close()
