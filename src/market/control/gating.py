"""M9.2 — Runtime Telemetry Integration & Signal Gating.

Connects the M9.1 ControlPlane to live system telemetry (M7 health/divergence/
silent-failures, M8 realized performance) and enforces the plane's decisions on
M5 PaperSignal publication.

Additive: reads M7/M8 outputs + M5 PaperSignal; never changes them. No
prediction/threshold/betting logic. Pure-stdlib, network-free, deterministic.

Silent mode preserves measurement: a DEGRADE/SUPPRESS/HALT decision blocks
*publication* only — the signal is still recorded so settlement (M8) and grading
(M8.5) continue downstream.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from enum import Enum
from typing import Dict, List, Optional

from .control_plane import (
    ControlPlane, ControlMetrics, ControlDecision, SystemState,
)

_GENESIS = "GENESIS"


class ReasonCode(str, Enum):
    ALLOWED = "ALLOWED"
    KILL_HALT = "KILL_HALT"
    STATE_OFF = "STATE_OFF"
    STATE_LOCKED = "STATE_LOCKED"
    SILENT_MODE_SHADOW = "SILENT_MODE_SHADOW"
    RISK_THROTTLE = "RISK_THROTTLE"
    LOW_TRUTH_CONFIDENCE = "LOW_TRUTH_CONFIDENCE"


def _hash(prev: str, payload: dict) -> str:
    return hashlib.sha256(f"{prev}|{json.dumps(payload, sort_keys=True)}".encode()).hexdigest()


def _clip(x, lo, hi):
    return max(lo, min(hi, x))


# ---------------------------------------------------------------------------
# 1. Telemetry adapter
# ---------------------------------------------------------------------------
class TelemetryAdapter:
    """Normalizes M7 + M8 outputs into a ControlMetrics snapshot."""

    @staticmethod
    def build(*, m7_health=None, m7_divergence=None, m7_silent_failures=None,
              m8_metrics=None, stability: float = 0.0, beat_rate: Optional[float] = None,
              data_coverage: float = 1.0, truth_lag_norm: float = 0.0,
              manual_kill: bool = False) -> ControlMetrics:
        health_v2 = float(getattr(m7_health, "composite", 0.0))
        cr = float(getattr(m7_divergence, "cr", 1.0)) if m7_divergence is not None else 1.0
        spg = float(getattr(m7_divergence, "spg", 0.0)) if m7_divergence is not None else 0.0
        clv = float(getattr(m8_metrics, "mean_clv", 0.0) or 0.0) if m8_metrics else 0.0
        roi = float(getattr(m8_metrics, "mean_roi", 0.0) or 0.0) if m8_metrics else 0.0
        sc = float(getattr(m8_metrics, "mean_settlement_confidence", 0.0) or 0.0) if m8_metrics else 0.0
        if beat_rate is None:
            beat_rate = 1.0 if clv > 0 else 0.0
        silent_critical = bool(m7_silent_failures)   # any flag present => critical
        return ControlMetrics(
            health_v2=health_v2, stability=stability, cr=cr, spg=spg,
            clv_realized=clv, beat_rate=beat_rate, roi_realized=roi,
            max_drawdown=0.0, settlement_confidence=sc,
            data_coverage=_clip(data_coverage, 0, 1), truth_lag_norm=max(0.0, truth_lag_norm),
            silent_failure_critical=silent_critical, manual_kill=manual_kill)


# ---------------------------------------------------------------------------
# 3. Gate result
# ---------------------------------------------------------------------------
@dataclass
class GateResult:
    signal_id: str
    state: str
    decision: str
    publish: bool          # may this signal be published externally (M6)?
    recorded: bool         # measurement pipeline continues regardless (always True)
    reason_codes: List[str]

    def to_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------------------
# 2+3+4+5. Control gateway
# ---------------------------------------------------------------------------
class ControlGateway:
    """Runs the control evaluation loop and gates M5 signal publication."""

    def __init__(self, plane: ControlPlane, db_path: str = ":memory:",
                 min_truth_confidence: float = 0.40) -> None:
        self.plane = plane
        self.min_truth_confidence = min_truth_confidence
        self.last_decision = ControlDecision.SUPPRESS
        self.last_state = plane.state
        self.last_metrics: Optional[ControlMetrics] = None
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS suppression_ledger (
                seq INTEGER PRIMARY KEY AUTOINCREMENT,
                signal_id TEXT, state TEXT, decision TEXT, publish INTEGER,
                reason_codes TEXT, at_iso TEXT, prev_hash TEXT, entry_hash TEXT
            );
            """
        )
        self.conn.commit()

    # -- evaluation loop step ----------------------------------------------
    def evaluate(self, metrics: ControlMetrics):
        out = self.plane.evaluate(metrics)
        self.last_decision = ControlDecision(out.decision)
        self.last_state = self.plane.state
        self.last_metrics = metrics
        return out

    # -- signal gating ------------------------------------------------------
    def gate(self, paper_signal, signal_id: Optional[str] = None) -> GateResult:
        sid = signal_id or self._sig_id(paper_signal)
        decision = self.last_decision
        state = self.last_state
        reasons: List[str] = []
        publish = False

        if decision == ControlDecision.HALT:
            reasons.append(ReasonCode.KILL_HALT.value)
        elif decision == ControlDecision.SUPPRESS:
            reasons.append(ReasonCode.STATE_LOCKED.value if state == SystemState.LOCKED
                           else ReasonCode.STATE_OFF.value)
        elif decision == ControlDecision.DEGRADE:
            reasons.append(ReasonCode.SILENT_MODE_SHADOW.value if state == SystemState.SHADOW
                           else ReasonCode.RISK_THROTTLE.value)
        else:  # ALLOW -> per-signal gates
            if paper_signal.truth_confidence < self.min_truth_confidence:
                reasons.append(ReasonCode.LOW_TRUTH_CONFIDENCE.value)
            else:
                publish = True
                reasons.append(ReasonCode.ALLOWED.value)

        result = GateResult(sid, state.value, decision.value, publish, True, reasons)
        self._record(result)
        return result

    def gate_batch(self, paper_signals, id_fn=None) -> List[GateResult]:
        """Deterministic ordering: process in the given sequence order."""
        out = []
        for s in paper_signals:
            out.append(self.gate(s, signal_id=id_fn(s) if id_fn else None))
        return out

    # -- monitoring ---------------------------------------------------------
    def monitor(self) -> dict:
        return {
            "active_state": self.plane.state.value,
            "last_decision": self.last_decision.value,
            "risk_index": self.plane.last_risk,
            "active_suppressions": self.plane.status()["active_suppressions"],
            "gate_failures": self._reason_counts(),
            "telemetry_snapshot": asdict(self.last_metrics) if self.last_metrics else None,
        }

    # -- replay / integrity -------------------------------------------------
    def replay(self) -> dict:
        rows = self.conn.execute("SELECT * FROM suppression_ledger ORDER BY seq").fetchall()
        published = sum(1 for r in rows if r["publish"])
        return {
            "n_gated": len(rows),
            "n_published": published,
            "n_suppressed": len(rows) - published,
            "reason_counts": self._reason_counts(),
            "chain_valid": self.verify_chain(),
        }

    def verify_chain(self) -> bool:
        rows = self.conn.execute("SELECT * FROM suppression_ledger ORDER BY seq").fetchall()
        prev = _GENESIS
        for r in rows:
            if r["prev_hash"] != prev or r["entry_hash"] != _hash(prev, self._payload(r)):
                return False
            prev = r["entry_hash"]
        return True

    # -- internals ----------------------------------------------------------
    def _record(self, r: GateResult) -> None:
        prev = self._last_hash()
        payload = {"signal_id": r.signal_id, "state": r.state, "decision": r.decision,
                   "publish": r.publish, "reason_codes": sorted(r.reason_codes)}
        entry_hash = _hash(prev, payload)
        self.conn.execute(
            "INSERT INTO suppression_ledger (signal_id,state,decision,publish,reason_codes,"
            "at_iso,prev_hash,entry_hash) VALUES (?,?,?,?,?,?,?,?)",
            (r.signal_id, r.state, r.decision, 1 if r.publish else 0,
             json.dumps(sorted(r.reason_codes)), datetime.now(timezone.utc).isoformat(),
             prev, entry_hash),
        )
        self.conn.commit()

    def _reason_counts(self) -> Dict[str, int]:
        counts: Dict[str, int] = {}
        for r in self.conn.execute("SELECT reason_codes FROM suppression_ledger").fetchall():
            for code in json.loads(r["reason_codes"]):
                counts[code] = counts.get(code, 0) + 1
        return counts

    def _payload(self, row: sqlite3.Row) -> dict:
        return {"signal_id": row["signal_id"], "state": row["state"],
                "decision": row["decision"], "publish": bool(row["publish"]),
                "reason_codes": json.loads(row["reason_codes"])}

    def _last_hash(self) -> str:
        row = self.conn.execute(
            "SELECT entry_hash FROM suppression_ledger ORDER BY seq DESC LIMIT 1").fetchone()
        return row["entry_hash"] if row else _GENESIS

    @staticmethod
    def _sig_id(paper_signal) -> str:
        return f"{paper_signal.match_id}:{paper_signal.market}:{paper_signal.selection}:{paper_signal.timestamp}"

    def close(self) -> None:
        self.conn.close()
