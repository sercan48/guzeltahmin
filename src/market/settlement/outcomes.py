"""M8.1 — Canonical outcome schema + market resolution.

Pure, deterministic, no network. Converts a match result into per-selection
settlement results. No ML, no betting/stake logic.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional


class OutcomeStatus(str, Enum):
    COMPLETED = "COMPLETED"      # played to settlement rules
    VOID = "VOID"
    CANCELLED = "CANCELLED"
    ABANDONED = "ABANDONED"
    POSTPONED = "POSTPONED"


# statuses that mean "do not settle as win/lose" -> VOID result
_VOID_LIKE = {OutcomeStatus.VOID, OutcomeStatus.CANCELLED,
              OutcomeStatus.ABANDONED, OutcomeStatus.POSTPONED}


class SettlementResult(str, Enum):
    WON = "WON"
    LOST = "LOST"
    PUSH = "PUSH"      # stake refund (e.g. integer O/U line, DNB draw)
    VOID = "VOID"      # match not settled (void/cancelled/abandoned)


@dataclass
class MatchOutcome:
    """Canonical match result (provider-agnostic, post-normalization)."""
    match_id: str
    status: OutcomeStatus
    home_goals: Optional[int] = None
    away_goals: Optional[int] = None
    source: str = "unknown"
    ingested_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def __post_init__(self):
        if isinstance(self.status, str):
            self.status = OutcomeStatus(self.status)
        if self.ingested_at.tzinfo is None:
            self.ingested_at = self.ingested_at.replace(tzinfo=timezone.utc)

    @property
    def total_goals(self) -> Optional[int]:
        if self.home_goals is None or self.away_goals is None:
            return None
        return self.home_goals + self.away_goals


def _ou_side_line(selection: str):
    """'OVER_2.5' -> ('OVER', 2.5); 'UNDER_2.5' -> ('UNDER', 2.5)."""
    parts = selection.upper().split("_", 1)
    if len(parts) != 2:
        return None, None
    try:
        return parts[0], float(parts[1])
    except ValueError:
        return None, None


def resolve_market(market: str, selection: str, outcome: MatchOutcome) -> SettlementResult:
    """Resolve a single (market, selection) against the canonical outcome.

    Supports 1X2, O/U (OVER_x / UNDER_x), DNB. Void-like statuses or
    unresolvable inputs return VOID (never a fabricated win/lose).
    """
    if outcome.status in _VOID_LIKE:
        return SettlementResult.VOID
    if outcome.status != OutcomeStatus.COMPLETED:
        return SettlementResult.VOID
    if outcome.home_goals is None or outcome.away_goals is None:
        return SettlementResult.VOID

    h, a = outcome.home_goals, outcome.away_goals
    sel = selection.upper()
    mk = market.upper()

    if mk in ("1X2", "MATCH_ODDS"):
        winner = "HOME" if h > a else ("AWAY" if a > h else "DRAW")
        return SettlementResult.WON if sel == winner else SettlementResult.LOST

    if mk in ("DNB", "DRAW_NO_BET"):
        if h == a:
            return SettlementResult.PUSH               # stake refunded
        winner = "HOME" if h > a else "AWAY"
        return SettlementResult.WON if sel == winner else SettlementResult.LOST

    if mk in ("O/U", "OU", "OVER_UNDER", "TOTALS"):
        side, line = _ou_side_line(selection)
        total = outcome.total_goals
        if side is None or line is None or total is None:
            return SettlementResult.VOID
        if total == line:
            return SettlementResult.PUSH               # integer-line push
        over = total > line
        won = (side == "OVER" and over) or (side == "UNDER" and not over)
        return SettlementResult.WON if won else SettlementResult.LOST

    # unknown market -> cannot resolve safely
    return SettlementResult.VOID
