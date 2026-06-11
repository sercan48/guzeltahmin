"""M9.1 — Production Control Plane Core.

Executable runtime governance: SYSTEM_STATE machine, promotion gates, kill switch,
risk index, control decisions, and an append-only hash-chained audit ledger.
Additive over M1-M8.5; governs only (no prediction/threshold/betting).
"""

from .control_plane import (
    SystemState,
    ControlDecision,
    ControlMetrics,
    ControlConfig,
    ControlOutcome,
    ControlPlane,
    kill_factors,
    risk_index,
    evaluate_gate,
)

__all__ = [
    "SystemState",
    "ControlDecision",
    "ControlMetrics",
    "ControlConfig",
    "ControlOutcome",
    "ControlPlane",
    "kill_factors",
    "risk_index",
    "evaluate_gate",
]
