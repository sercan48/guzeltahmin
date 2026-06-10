"""M8.3 — Settlement Mathematics & Error Decomposition Engine.

Connects M8.1 settlement outcomes with M8.2 locked closing truth and finalizes
per-settlement metrics into an append-only, hash-chained, replay-safe ledger.

No prediction logic, no ML, no betting/Kelly. Model probability is an injected
input; the engine only *measures*.

Error decomposition (prob space, telescoping => balances exactly):
    p_entry = 1 / o_entry      p_close = 1 / o_close (locked, M8.2)
    total_error       = p_model − y
    calibration_error = p_model − p_entry      (model vs the entered market price)
    execution_error   = p_entry − p_close      (entry vs close: timing / CLV component)
    truth_error       = p_close − y            (sharp close vs reality)
    residual          = total_error − (calibration + execution + truth)  ≡ 0  (balance check)
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from enum import Enum
from typing import Dict, List, Optional

_GENESIS = "GENESIS"


class MetricStatus(str, Enum):
    COMPLETED = "COMPLETED"
    VOID = "VOID"
    MISSING_CLOSE = "MISSING_CLOSE"
    MISSING_OUTCOME = "MISSING_OUTCOME"   # PENDING


# settlement-confidence weights (positive -> monotone in each component)
_W_OUTCOME, _W_CLOSE, _W_TRUTH = 0.40, 0.35, 0.25
_STALE_PENALTY = 0.80


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _hash(prev_hash: str, payload: dict) -> str:
    body = json.dumps(payload, sort_keys=True)
    return hashlib.sha256(f"{prev_hash}|{body}".encode("utf-8")).hexdigest()


def _clip01(x: float) -> float:
    return max(0.0, min(1.0, x))


def _r(x, nd=6):
    return None if x is None else round(x, nd)


@dataclass
class MetricRecord:
    metric_id: str
    match_id: str
    market: str
    selection: str
    entry_odds: float
    o_close: Optional[float]
    p_model: Optional[float]
    y: Optional[int]
    result: Optional[str]
    status: str
    realized_roi: Optional[float]
    realized_clv: Optional[float]
    calibration_error: Optional[float]
    execution_error: Optional[float]
    truth_error: Optional[float]
    residual: Optional[float]
    outcome_conf: float
    close_conf: float
    truth_conf: float
    settlement_confidence: float
    is_void: bool
    prev_hash: str = ""
    entry_hash: str = ""
    seq: Optional[int] = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class RollingStats:
    window: int
    n: int
    moving_roi: Optional[float]
    moving_clv: Optional[float]
    conf_weighted_roi: Optional[float]
    conf_weighted_clv: Optional[float]

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class MetricsSummary:
    n_metrics: int
    n_void: int
    n_pending: int
    total_roi: float
    mean_roi: Optional[float]
    mean_clv: Optional[float]
    mean_settlement_confidence: Optional[float]
    mean_abs_residual: Optional[float]
    chain_valid: bool

    def to_dict(self) -> dict:
        return asdict(self)


def settlement_confidence(outcome_conf: float, close_conf: float,
                          truth_conf: float, is_stale: bool = False) -> float:
    """Composite settlement confidence. Monotone in each component."""
    base = _W_OUTCOME * _clip01(outcome_conf) + _W_CLOSE * _clip01(close_conf) \
        + _W_TRUTH * _clip01(truth_conf)
    if is_stale:
        base *= _STALE_PENALTY
    return round(_clip01(base), 6)


class SettlementMathEngine:
    """Append-only, hash-chained settlement-metrics ledger + math."""

    def __init__(self, db_path: str = ":memory:") -> None:
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS settlement_metrics (
                seq INTEGER PRIMARY KEY AUTOINCREMENT,
                metric_id TEXT UNIQUE, match_id TEXT, market TEXT, selection TEXT,
                entry_odds REAL, o_close REAL, p_model REAL, y INTEGER, result TEXT,
                status TEXT, realized_roi REAL, realized_clv REAL,
                calibration_error REAL, execution_error REAL, truth_error REAL, residual REAL,
                outcome_conf REAL, close_conf REAL, truth_conf REAL,
                settlement_confidence REAL, is_void INTEGER,
                computed_at_iso TEXT, prev_hash TEXT, entry_hash TEXT
            );
            """
        )
        self.conn.commit()

    # -- finalize one settlement (idempotent) ------------------------------
    def finalize(self, metric_id: str, match_id: str, market: str, selection: str,
                 entry_odds: float, *, result: Optional[str], y: Optional[int],
                 o_close: Optional[float], p_close: Optional[float],
                 p_model: Optional[float], outcome_conf: float, close_conf: float,
                 truth_conf: float, is_stale: bool = False) -> MetricRecord:
        existing = self.conn.execute(
            "SELECT * FROM settlement_metrics WHERE metric_id=?", (metric_id,)
        ).fetchone()
        if existing:
            return self._row_to_record(existing)          # idempotent

        is_void = result == "VOID"
        if result is None:
            status = MetricStatus.MISSING_OUTCOME
        elif is_void:
            status = MetricStatus.VOID
        elif o_close is None:
            status = MetricStatus.MISSING_CLOSE
        else:
            status = MetricStatus.COMPLETED

        # realized ROI (flat unit, paper): WON o-1, LOST -1, else 0/None
        if result is None:
            roi = None
        elif result == "WON":
            roi = entry_odds - 1.0
        elif result == "LOST":
            roi = -1.0
        else:
            roi = 0.0   # PUSH / VOID

        clv = (entry_odds / o_close - 1.0) if (o_close and o_close > 0) else None

        # error decomposition only when we have a binary outcome + close + model
        cal = exe = tru = res = None
        if y in (0, 1) and o_close and p_close is not None and p_model is not None:
            p_entry = 1.0 / entry_odds
            cal = p_model - p_entry
            exe = p_entry - p_close
            tru = p_close - y
            total = p_model - y
            res = total - (cal + exe + tru)            # ~0 balance check

        sconf = settlement_confidence(
            outcome_conf if result is not None else 0.0,
            close_conf if o_close is not None else 0.0,
            truth_conf, is_stale)

        return self._persist(MetricRecord(
            metric_id, match_id, market, selection, entry_odds, o_close, p_model, y,
            result, status.value, _r(roi), _r(clv), _r(cal), _r(exe), _r(tru), _r(res),
            round(_clip01(outcome_conf), 6), round(_clip01(close_conf), 6),
            round(_clip01(truth_conf), 6), sconf, is_void))

    # -- integration: pull from M8.1 settlement + M8.2 closure -------------
    def finalize_from_ledgers(self, settlement_ledger, closure_ledger, *, bet_id: str,
                              match_id: str, market: str, selection: str,
                              entry_odds: float, p_model: Optional[float],
                              truth_conf: float) -> MetricRecord:
        """Connect M8.1 (outcome/result) + M8.2 (locked close)."""
        close = closure_ledger.get_close(match_id, market, selection)
        o_close = close.o_close if close else None
        p_close = close.p_close if close else None
        close_conf = (close.confidence or 0.0) if close else 0.0
        is_stale = bool(close.is_stale) if close else False

        settle_rec = settlement_ledger.settle(bet_id, match_id, market, selection,
                                               entry_odds, closing_odds=o_close)
        if settle_rec is None:
            result, y, outcome_conf = None, None, 0.0
        else:
            result = settle_rec.result
            y = 1 if result == "WON" else (0 if result == "LOST" else None)
            outcome_conf = 0.0 if result is None else 1.0

        metric_id = f"{match_id}:{market}:{selection}:{bet_id}"
        return self.finalize(metric_id, match_id, market, selection, entry_odds,
                             result=result, y=y, o_close=o_close, p_close=p_close,
                             p_model=p_model, outcome_conf=outcome_conf,
                             close_conf=close_conf, truth_conf=truth_conf,
                             is_stale=is_stale)

    # -- rolling statistics -------------------------------------------------
    def rolling(self, window: int = 100) -> RollingStats:
        rows = self.conn.execute(
            "SELECT realized_roi, realized_clv, settlement_confidence, is_void, status "
            "FROM settlement_metrics ORDER BY seq DESC LIMIT ?", (window,)
        ).fetchall()
        roi = [(r["realized_roi"], r["settlement_confidence"]) for r in rows
               if not r["is_void"] and r["realized_roi"] is not None]
        clv = [(r["realized_clv"], r["settlement_confidence"]) for r in rows
               if not r["is_void"] and r["realized_clv"] is not None]
        return RollingStats(
            window=window, n=len(rows),
            moving_roi=self._mean([v for v, _ in roi]),
            moving_clv=self._mean([v for v, _ in clv]),
            conf_weighted_roi=self._wmean(roi),
            conf_weighted_clv=self._wmean(clv),
        )

    # -- replay / integrity -------------------------------------------------
    def replay(self) -> MetricsSummary:
        rows = self.conn.execute("SELECT * FROM settlement_metrics ORDER BY seq").fetchall()
        roi = [r["realized_roi"] for r in rows
               if not r["is_void"] and r["realized_roi"] is not None]
        clv = [r["realized_clv"] for r in rows
               if not r["is_void"] and r["realized_clv"] is not None]
        sconf = [r["settlement_confidence"] for r in rows]
        resid = [abs(r["residual"]) for r in rows if r["residual"] is not None]
        return MetricsSummary(
            n_metrics=len(rows),
            n_void=sum(1 for r in rows if r["is_void"]),
            n_pending=sum(1 for r in rows if r["status"] == MetricStatus.MISSING_OUTCOME.value),
            total_roi=round(sum(roi), 6),
            mean_roi=self._mean(roi), mean_clv=self._mean(clv),
            mean_settlement_confidence=self._mean(sconf),
            mean_abs_residual=self._mean(resid),
            chain_valid=self.verify_chain(),
        )

    def verify_chain(self) -> bool:
        rows = self.conn.execute("SELECT * FROM settlement_metrics ORDER BY seq").fetchall()
        prev = _GENESIS
        for r in rows:
            if r["prev_hash"] != prev or r["entry_hash"] != _hash(prev, self._payload(r)):
                return False
            prev = r["entry_hash"]
        return True

    # -- internals ----------------------------------------------------------
    def _persist(self, rec: MetricRecord) -> MetricRecord:
        rec.prev_hash = self._last_hash()
        rec.entry_hash = _hash(rec.prev_hash, self._payload_from_record(rec))
        self.conn.execute(
            "INSERT INTO settlement_metrics (metric_id,match_id,market,selection,entry_odds,"
            "o_close,p_model,y,result,status,realized_roi,realized_clv,calibration_error,"
            "execution_error,truth_error,residual,outcome_conf,close_conf,truth_conf,"
            "settlement_confidence,is_void,computed_at_iso,prev_hash,entry_hash) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (rec.metric_id, rec.match_id, rec.market, rec.selection, rec.entry_odds,
             rec.o_close, rec.p_model, rec.y, rec.result, rec.status, rec.realized_roi,
             rec.realized_clv, rec.calibration_error, rec.execution_error, rec.truth_error,
             rec.residual, rec.outcome_conf, rec.close_conf, rec.truth_conf,
             rec.settlement_confidence, 1 if rec.is_void else 0, _now_iso(),
             rec.prev_hash, rec.entry_hash),
        )
        self.conn.commit()
        rec.seq = self.conn.execute("SELECT MAX(seq) s FROM settlement_metrics").fetchone()["s"]
        return rec

    @staticmethod
    def _payload_from_record(rec: MetricRecord) -> dict:
        return {
            "metric_id": rec.metric_id, "result": rec.result, "status": rec.status,
            "realized_roi": rec.realized_roi, "realized_clv": rec.realized_clv,
            "calibration_error": rec.calibration_error, "execution_error": rec.execution_error,
            "truth_error": rec.truth_error, "residual": rec.residual,
            "settlement_confidence": rec.settlement_confidence, "is_void": rec.is_void,
        }

    def _payload(self, row: sqlite3.Row) -> dict:
        return {
            "metric_id": row["metric_id"], "result": row["result"], "status": row["status"],
            "realized_roi": row["realized_roi"], "realized_clv": row["realized_clv"],
            "calibration_error": row["calibration_error"], "execution_error": row["execution_error"],
            "truth_error": row["truth_error"], "residual": row["residual"],
            "settlement_confidence": row["settlement_confidence"], "is_void": bool(row["is_void"]),
        }

    def _last_hash(self) -> str:
        row = self.conn.execute(
            "SELECT entry_hash FROM settlement_metrics ORDER BY seq DESC LIMIT 1"
        ).fetchone()
        return row["entry_hash"] if row else _GENESIS

    @staticmethod
    def _row_to_record(row: sqlite3.Row) -> MetricRecord:
        rec = MetricRecord(
            metric_id=row["metric_id"], match_id=row["match_id"], market=row["market"],
            selection=row["selection"], entry_odds=row["entry_odds"], o_close=row["o_close"],
            p_model=row["p_model"], y=row["y"], result=row["result"], status=row["status"],
            realized_roi=row["realized_roi"], realized_clv=row["realized_clv"],
            calibration_error=row["calibration_error"], execution_error=row["execution_error"],
            truth_error=row["truth_error"], residual=row["residual"],
            outcome_conf=row["outcome_conf"], close_conf=row["close_conf"],
            truth_conf=row["truth_conf"], settlement_confidence=row["settlement_confidence"],
            is_void=bool(row["is_void"]), prev_hash=row["prev_hash"], entry_hash=row["entry_hash"],
        )
        rec.seq = row["seq"]
        return rec

    @staticmethod
    def _mean(xs):
        return round(sum(xs) / len(xs), 6) if xs else None

    @staticmethod
    def _wmean(pairs):
        wsum = sum(w for _, w in pairs)
        return round(sum(v * w for v, w in pairs) / wsum, 6) if wsum > 0 else None

    def close(self) -> None:
        self.conn.close()
