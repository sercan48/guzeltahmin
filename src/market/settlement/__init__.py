"""M8.1 — Outcome ingestion & settlement ledger.

Additive settlement infrastructure: canonical outcomes, market resolution, and a
SQLite append-only hash-chained ledger with idempotent, replay-safe settlement
and realized ROI/CLV. No prediction/ML, no betting/stake logic.
"""

from .outcomes import (
    OutcomeStatus,
    SettlementResult,
    MatchOutcome,
    resolve_market,
)
from .ledger import (
    SettlementLedger,
    SettlementRecord,
    SettlementSummary,
)
from .closure import (
    ClosureLedger,
    ClosureRecord,
    ClosureSummary,
    CloseKind,
)
from .math_engine import (
    SettlementMathEngine,
    MetricRecord,
    MetricsSummary,
    RollingStats,
    MetricStatus,
    settlement_confidence,
)

__all__ = [
    "OutcomeStatus",
    "SettlementResult",
    "MatchOutcome",
    "resolve_market",
    "SettlementLedger",
    "SettlementRecord",
    "SettlementSummary",
    "ClosureLedger",
    "ClosureRecord",
    "ClosureSummary",
    "CloseKind",
    "SettlementMathEngine",
    "MetricRecord",
    "MetricsSummary",
    "RollingStats",
    "MetricStatus",
    "settlement_confidence",
]
