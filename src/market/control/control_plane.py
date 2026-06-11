"""M9.1 — Production Control Plane Core (executable).

Implements the runtime governance core of the M9 design: the global SYSTEM_STATE
machine, deterministic promotion gates, a multi-factor kill switch, a bounded
risk index, control decisions (ALLOW/DEGRADE/SUPPRESS/HALT), and an append-only
hash-chained audit ledger.

Additive: reads a runtime telemetry snapshot (ControlMetrics) supplied by the
data plane (M7 health, M8 realized metrics, M2 truth freshness). It governs —
it never computes a prediction, edge, threshold, or stake. Pure-stdlib,
network-free, deterministic.

Invariants:
- the kill switch ALWAYS dominates promotion (kill => LOCKED, never promote)
- LOCKED never auto-promotes (only manual_reset, and only with no kill factors)
- control decisions are deterministic given metrics (replay-identical)
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from typing import Dict, List, Optional, Tuple

_GENESIS = "GENESIS"


class SystemState(str, Enum):
    OFF = "OFF"
    SHADOW = "SHADOW"
    PAPER = "PAPER"
    MICRO = "MICRO"
    LIVE = "LIVE"
    LOCKED = "LOCKED"


# promotion ladder (LOCKED is off-ladder)
_LADDER = [SystemState.OFF, SystemState.SHADOW, SystemState.PAPER,
           SystemState.MICRO, SystemState.LIVE]


class ControlDecision(str, Enum):
    ALLOW = "ALLOW"        # publish / act per state
    DEGRADE = "DEGRADE"    # silent mode: compute + record, no publish
    SUPPRESS = "SUPPRESS"  # no action (OFF / LOCKED)
    HALT = "HALT"          # kill fired -> LOCKED


@dataclass
class ControlMetrics:
    """Runtime telemetry snapshot feeding the control plane."""
    health_v2: float = 0.0            # 0..100  (M7 health v2)
    stability: float = 0.0            # 0..100  (M7 stability)
    cr: float = 0.0                   # 0..1    shadow<->paper consistency
    spg: float = 0.0                  # >=0     shadow-paper gap
    clv_realized: float = 0.0         # mean realized CLV (M8)
    beat_rate: float = 0.0            # 0..1    % beat close
    roi_realized: float = 0.0         # realized ROI (M8)
    max_drawdown: float = 0.0         # 0..1    fraction
    settlement_confidence: float = 0.0  # 0..1
    data_coverage: float = 0.0        # 0..1    completeness
    truth_lag_norm: float = 0.0       # 0..1    (>=1 => exceeded budget)
    silent_failure_critical: bool = False
    manual_kill: bool = False


# threshold-key -> (metric attribute, comparison)
_GATE_SCHEMA: Dict[str, Tuple[str, str]] = {
    "health_min": ("health_v2", "ge"),
    "stability_min": ("stability", "ge"),
    "cr_min": ("cr", "ge"),
    "spg_max": ("spg", "le"),
    "clv_min": ("clv_realized", "ge"),
    "beat_min": ("beat_rate", "ge"),
    "roi_min": ("roi_realized", "ge"),
    "dd_max": ("max_drawdown", "le"),
    "sc_min": ("settlement_confidence", "ge"),
    "cov_min": ("data_coverage", "ge"),
    "lag_max": ("truth_lag_norm", "le"),
}


def _cmp(value: float, op: str, threshold: float) -> bool:
    return value >= threshold if op == "ge" else value <= threshold


@dataclass
class ControlConfig:
    # promotion gates: target state -> threshold dict
    promote: Dict[str, Dict[str, float]] = field(default_factory=lambda: {
        SystemState.SHADOW.value: {"health_min": 50, "cov_min": 0.8, "lag_max": 1.0},
        SystemState.PAPER.value: {"health_min": 60, "cr_min": 0.80, "spg_max": 0.02,
                                   "cov_min": 0.8, "sc_min": 0.6, "lag_max": 1.0},
        SystemState.MICRO.value: {"health_min": 75, "cr_min": 0.85, "spg_max": 0.02,
                                   "clv_min": 0.0, "beat_min": 0.52, "roi_min": 0.0,
                                   "dd_max": 0.15, "sc_min": 0.8, "cov_min": 0.8, "lag_max": 1.0},
        SystemState.LIVE.value: {"health_min": 85, "cr_min": 0.85, "spg_max": 0.02,
                                  "clv_min": 0.0, "beat_min": 0.53, "roi_min": 0.0,
                                  "dd_max": 0.10, "sc_min": 0.85, "cov_min": 0.85, "lag_max": 1.0},
    })
    # demotion exit gates: current state -> threshold dict (breach => demote)
    exit_floor: Dict[str, Dict[str, float]] = field(default_factory=lambda: {
        SystemState.SHADOW.value: {"health_min": 40, "cov_min": 0.6},
        SystemState.PAPER.value: {"health_min": 55},
        SystemState.MICRO.value: {"health_min": 70, "dd_max": 0.18, "roi_min": 0.0},
        SystemState.LIVE.value: {"health_min": 82, "dd_max": 0.12, "roi_min": 0.0},
    })
    # kill floors
    kill_health_floor: float = 30.0
    kill_dd: float = 0.25
    kill_spg: float = 0.10
    kill_cr_floor: float = 0.50
    kill_sc_floor: float = 0.30
    # risk index
    risk_weights: Dict[str, float] = field(default_factory=lambda: {
        "health": 0.30, "dd": 0.25, "drift": 0.20, "lag": 0.15, "settle": 0.10})
    throttle_risk: float = 60.0       # risk_index >= this -> DEGRADE


@dataclass
class ControlOutcome:
    state: str
    decision: str
    risk_index: float
    kill: bool
    kill_factors: Dict[str, bool]
    promotion_gate_passed: Optional[bool]
    transition: Optional[Tuple[str, str, str]]   # (from, to, type)
    active_suppressions: List[str]

    def to_dict(self) -> dict:
        return asdict(self)


def _clip(x, lo, hi):
    return max(lo, min(hi, x))


def kill_factors(m: ControlMetrics, cfg: ControlConfig) -> Dict[str, bool]:
    return {
        "k_health": m.health_v2 < cfg.kill_health_floor,
        "k_dd": m.max_drawdown > cfg.kill_dd,
        "k_truthlag": m.truth_lag_norm >= 1.0,
        "k_drift": (m.spg > cfg.kill_spg) or (m.cr < cfg.kill_cr_floor),
        "k_silent": bool(m.silent_failure_critical),
        "k_settle": m.settlement_confidence < cfg.kill_sc_floor,
        "k_manual": bool(m.manual_kill),
    }


def risk_index(m: ControlMetrics, cfg: ControlConfig, kill: bool) -> float:
    if kill:
        return 100.0
    r = {
        "health": _clip(1.0 - m.health_v2 / 100.0, 0, 1),
        "dd": _clip(m.max_drawdown / cfg.kill_dd, 0, 1),
        "drift": _clip(max(m.spg / cfg.kill_spg, 1.0 - m.cr), 0, 1),
        "lag": _clip(m.truth_lag_norm, 0, 1),
        "settle": _clip(1.0 - m.settlement_confidence, 0, 1),
    }
    w = cfg.risk_weights
    return round(100.0 * sum(w[k] * r[k] for k in w), 4)


def evaluate_gate(thresholds: Dict[str, float], m: ControlMetrics) -> Tuple[bool, Dict[str, bool]]:
    detail: Dict[str, bool] = {}
    for key, thr in thresholds.items():
        attr, op = _GATE_SCHEMA[key]
        detail[key] = _cmp(getattr(m, attr), op, thr)
    return (all(detail.values()) if detail else True), detail


def _hash(prev: str, payload: dict) -> str:
    return hashlib.sha256(f"{prev}|{json.dumps(payload, sort_keys=True)}".encode()).hexdigest()


class ControlPlane:
    """Executable control plane with an append-only hash-chained audit ledger."""

    def __init__(self, db_path: str = ":memory:", config: ControlConfig = ControlConfig(),
                 initial_state: SystemState = SystemState.OFF) -> None:
        self.cfg = config
        self.state = initial_state
        self.last_risk = 0.0
        self.last_kill_factors: Dict[str, bool] = {}
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS control_audit (
                seq INTEGER PRIMARY KEY AUTOINCREMENT,
                event_type TEXT, from_state TEXT, to_state TEXT, decision TEXT,
                risk_index REAL, kill INTEGER, reason TEXT, at_iso TEXT,
                prev_hash TEXT, entry_hash TEXT
            );
            """
        )
        self.conn.commit()

    # -- main evaluation ----------------------------------------------------
    def evaluate(self, m: ControlMetrics) -> ControlOutcome:
        kf = kill_factors(m, self.cfg)
        kill = any(kf.values())
        risk = risk_index(m, self.cfg, kill)
        self.last_risk, self.last_kill_factors = risk, kf

        transition: Optional[Tuple[str, str, str]] = None
        gate_passed: Optional[bool] = None

        # 1. kill ALWAYS dominates -> LOCKED
        if kill:
            if self.state != SystemState.LOCKED:
                transition = self._transition(SystemState.LOCKED, "KILL", risk, kill,
                                              self._kill_reason(kf))
            decision = ControlDecision.HALT
            return self._finish(decision, risk, kill, kf, gate_passed, transition)

        # 2. LOCKED never auto-promotes
        if self.state == SystemState.LOCKED:
            decision = ControlDecision.SUPPRESS
            self._audit("DECISION", self.state, self.state, decision, risk, kill, "locked")
            return self._finish(decision, risk, kill, kf, gate_passed, None)

        # 3. demotion (fast) takes precedence over promotion
        if self._exit_breached(m):
            target = self._lower(self.state)
            transition = self._transition(target, "DEMOTE", risk, kill, "exit floor breached")
        else:
            # 4. promotion (one rung, gate-gated)
            nxt = self._higher(self.state)
            if nxt is not None:
                gate_passed, _ = evaluate_gate(self.cfg.promote[nxt.value], m)
                if gate_passed:
                    transition = self._transition(nxt, "PROMOTE", risk, kill, "gate passed")

        decision = self._decision(self.state, risk)
        self._audit("DECISION", self.state, self.state, decision, risk, kill, "")
        return self._finish(decision, risk, kill, kf, gate_passed, transition)

    # -- manual controls ----------------------------------------------------
    def force_kill(self, reason: str = "manual") -> ControlOutcome:
        transition = None
        if self.state != SystemState.LOCKED:
            transition = self._transition(SystemState.LOCKED, "KILL", self.last_risk, True,
                                          f"manual:{reason}")
        return self._finish(ControlDecision.HALT, 100.0, True,
                            {"k_manual": True}, None, transition)

    def manual_reset(self, m: ControlMetrics) -> bool:
        """LOCKED -> SHADOW, only if no kill factors are active."""
        if self.state != SystemState.LOCKED:
            return False
        if any(kill_factors(m, self.cfg).values()):
            return False
        self._transition(SystemState.SHADOW, "RESET", self.last_risk, False, "manual reset")
        return True

    # -- monitoring ---------------------------------------------------------
    def status(self) -> dict:
        last = self.conn.execute(
            "SELECT * FROM control_audit ORDER BY seq DESC LIMIT 1").fetchone()
        nxt = self._higher(self.state)
        active_gates = self.cfg.promote.get(nxt.value, {}) if nxt else {}
        return {
            "current_state": self.state.value,
            "risk_index": self.last_risk,
            "kill_factors_active": [k for k, v in self.last_kill_factors.items() if v],
            "next_promotion_target": nxt.value if nxt else None,
            "active_gates": active_gates,
            "active_suppressions": self._suppressions(self._decision(self.state, self.last_risk)),
            "last_transition": (
                {"from": last["from_state"], "to": last["to_state"],
                 "type": last["event_type"], "at": last["at_iso"]}
                if last else None),
        }

    # -- replay / integrity -------------------------------------------------
    def replay(self) -> dict:
        rows = self.conn.execute("SELECT * FROM control_audit ORDER BY seq").fetchall()
        state = SystemState.OFF.value
        decisions: Dict[str, int] = {}
        for r in rows:
            if r["to_state"]:
                state = r["to_state"]
            decisions[r["decision"]] = decisions.get(r["decision"], 0) + 1
        return {"final_state": state, "n_events": len(rows),
                "decision_counts": decisions, "chain_valid": self.verify_chain()}

    def verify_chain(self) -> bool:
        rows = self.conn.execute("SELECT * FROM control_audit ORDER BY seq").fetchall()
        prev = _GENESIS
        for r in rows:
            if r["prev_hash"] != prev or r["entry_hash"] != _hash(prev, self._payload(r)):
                return False
            prev = r["entry_hash"]
        return True

    # -- internals ----------------------------------------------------------
    def _decision(self, state: SystemState, risk: float) -> ControlDecision:
        if state == SystemState.OFF:
            return ControlDecision.SUPPRESS
        if state == SystemState.LOCKED:
            return ControlDecision.SUPPRESS
        if state == SystemState.SHADOW:
            return ControlDecision.DEGRADE        # silent mode
        if risk >= self.cfg.throttle_risk:
            return ControlDecision.DEGRADE
        return ControlDecision.ALLOW

    def _suppressions(self, decision: ControlDecision) -> List[str]:
        return {
            ControlDecision.ALLOW: [],
            ControlDecision.DEGRADE: ["silent_mode"],
            ControlDecision.SUPPRESS: ["no_publish"],
            ControlDecision.HALT: ["halted"],
        }[decision]

    def _exit_breached(self, m: ControlMetrics) -> bool:
        floor = self.cfg.exit_floor.get(self.state.value)
        if not floor:
            return False
        passed, _ = evaluate_gate(floor, m)
        return not passed     # breach = floor gate fails

    @staticmethod
    def _higher(state: SystemState) -> Optional[SystemState]:
        if state not in _LADDER:
            return None
        i = _LADDER.index(state)
        return _LADDER[i + 1] if i + 1 < len(_LADDER) else None

    @staticmethod
    def _lower(state: SystemState) -> SystemState:
        if state not in _LADDER:
            return state
        i = _LADDER.index(state)
        return _LADDER[i - 1] if i > 0 else state

    @staticmethod
    def _kill_reason(kf: Dict[str, bool]) -> str:
        return "kill:" + ",".join(k for k, v in kf.items() if v)

    def _transition(self, to: SystemState, etype: str, risk: float, kill: bool,
                    reason: str) -> Tuple[str, str, str]:
        frm = self.state.value
        self.state = to
        self._audit(etype, frm, to.value, self._decision(to, risk).value, risk, kill, reason)
        return (frm, to.value, etype)

    def _audit(self, etype, frm, to, decision, risk, kill, reason) -> None:
        decision = decision.value if isinstance(decision, ControlDecision) else decision
        prev = self._last_hash()
        payload = {"event_type": etype, "from_state": frm, "to_state": to,
                   "decision": decision, "risk_index": round(risk, 4),
                   "kill": bool(kill), "reason": reason}
        entry_hash = _hash(prev, payload)
        self.conn.execute(
            "INSERT INTO control_audit (event_type,from_state,to_state,decision,risk_index,"
            "kill,reason,at_iso,prev_hash,entry_hash) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (etype, frm, to, decision, round(risk, 4), 1 if kill else 0, reason,
             datetime.now(timezone.utc).isoformat(), prev, entry_hash),
        )
        self.conn.commit()

    def _payload(self, row: sqlite3.Row) -> dict:
        return {"event_type": row["event_type"], "from_state": row["from_state"],
                "to_state": row["to_state"], "decision": row["decision"],
                "risk_index": round(row["risk_index"], 4), "kill": bool(row["kill"]),
                "reason": row["reason"]}

    def _last_hash(self) -> str:
        row = self.conn.execute(
            "SELECT entry_hash FROM control_audit ORDER BY seq DESC LIMIT 1").fetchone()
        return row["entry_hash"] if row else _GENESIS

    def _finish(self, decision, risk, kill, kf, gate_passed, transition) -> ControlOutcome:
        return ControlOutcome(
            state=self.state.value, decision=decision.value, risk_index=risk, kill=kill,
            kill_factors=kf, promotion_gate_passed=gate_passed, transition=transition,
            active_suppressions=self._suppressions(decision))

    def close(self) -> None:
        self.conn.close()
