"""R1.1-compatible odds schema for the measurement layer.

This mirrors the ``OddsRecord`` produced by the R1.1 Provider Abstraction
Layer (``src/db/providers/core.py``). When the providers package is present in
the repo, the measurement layer will transparently consume those records — the
fields below are a strict superset-compatible subset:

    match_id, bookmaker, market, selection, odds, timestamp,
    snapshot_type, source_id, confidence_score

We define it locally so the measurement layer is self-contained and runnable
even before the providers code lands. No behaviour depends on importing the
real provider classes; only the field contract matters.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import NamedTuple, Optional


class MarketType(str, Enum):
    """Canonical market identifiers.

    Values are aligned with the PAL Core contract (docs/MIW_R1_PAL_CORE.md) so
    a real PAL ``OddsRecord.market`` maps here losslessly. DNB is kept as a
    measurement-layer extension (it appeared in the R1.1 run).
    """

    ONE_X_TWO = "1X2"
    OVER_UNDER = "O/U"
    BTTS = "BTTS"
    ASIAN_HANDICAP = "ASIAN_HANDICAP"
    DNB = "DNB"  # extension; not in PAL Core enum


class SnapshotType(str, Enum):
    """Lifecycle stage of a snapshot relative to kickoff.

    Values match the PAL Core contract (``"T-24h"`` etc.). OPEN..T1H are
    *pre-match* horizons; CLOSE is the final pre-kickoff line (a future
    placeholder in R1.2 — populated by the R2/R3 scheduler). LIVE is a
    measurement-layer extension for in-play quotes (PAL emits these via
    ``fetch_odds`` rather than a dedicated snapshot_type).
    """

    OPEN = "OPEN"
    T24H = "T-24h"
    T12H = "T-12h"
    T6H = "T-6h"
    T1H = "T-1h"
    LIVE = "LIVE"   # extension
    CLOSE = "CLOSE"


class Horizon(str, Enum):
    """Time-to-kickoff anchors used by the time-series builder."""

    OPENING = "opening"
    H24 = "24h"
    H12 = "12h"
    H6 = "6h"
    H1 = "1h"
    LIVE = "live"
    CLOSING = "closing"

    @property
    def hours_before_ko(self) -> Optional[float]:
        """Target hours before kickoff for fixed pre-match anchors.

        OPENING/LIVE/CLOSING are not fixed-offset anchors and return None.
        """
        return {
            Horizon.H24: 24.0,
            Horizon.H12: 12.0,
            Horizon.H6: 6.0,
            Horizon.H1: 1.0,
        }.get(self)


# Ordered pre-match horizons from earliest to latest (strict ordering anchor).
PREMATCH_HORIZONS = (Horizon.H24, Horizon.H12, Horizon.H6, Horizon.H1)


class MarketKey(NamedTuple):
    """Identity of a single tradable line: one match, one market, one selection.

    A time-series is built per MarketKey. ``bookmaker`` is optional: when None
    the series is a cross-book consensus stream; when set it is a single-book
    stream (needed for bookmaker-adjusted CLV and disagreement metrics).
    """

    match_id: str
    market: str
    selection: str
    bookmaker: Optional[str] = None


@dataclass(frozen=True)
class OddsRecord:
    """A single point-in-time decimal-odds observation.

    Field contract is identical to R1.1's OddsRecord. Frozen so records are
    safe to dedup/hash and cannot be mutated mid-pipeline (point-in-time
    integrity).
    """

    match_id: str
    bookmaker: str
    market: str
    selection: str
    odds: float
    timestamp: datetime
    snapshot_type: str = SnapshotType.OPEN.value
    source_id: str = "unknown"
    confidence_score: float = 0.5

    def __post_init__(self) -> None:
        # Enforce tz-aware UTC timestamps (R1.1 normalizes everything to UTC).
        if self.timestamp.tzinfo is None:
            object.__setattr__(
                self, "timestamp", self.timestamp.replace(tzinfo=timezone.utc)
            )
        else:
            object.__setattr__(
                self, "timestamp", self.timestamp.astimezone(timezone.utc)
            )

    # --- PAL interop -------------------------------------------------------
    @classmethod
    def from_pal(cls, pal_record) -> "OddsRecord":
        """Adapt a PAL Core OddsRecord (pydantic) into a measurement record.

        Duck-typed: accepts any object exposing the PAL field contract
        (match_id, bookmaker, market, selection, odds, timestamp,
        snapshot_type, source_id, confidence_score). ``market`` /
        ``snapshot_type`` may be PAL enums or plain strings; we normalize to
        their string value so the measurement layer never depends on importing
        pydantic or the PAL classes.
        """
        def _val(x):
            return getattr(x, "value", x)

        return cls(
            match_id=pal_record.match_id,
            bookmaker=pal_record.bookmaker,
            market=_val(pal_record.market),
            selection=pal_record.selection,
            odds=float(pal_record.odds),
            timestamp=pal_record.timestamp,
            snapshot_type=_val(pal_record.snapshot_type),
            source_id=getattr(pal_record, "source_id", "unknown"),
            confidence_score=getattr(pal_record, "confidence_score", 0.5),
        )

    # --- derived, leakage-free helpers -------------------------------------
    @property
    def key(self) -> MarketKey:
        return MarketKey(self.match_id, self.market, self.selection, self.bookmaker)

    @property
    def implied_prob(self) -> float:
        """Raw (vig-inclusive) implied probability = 1 / odds.

        De-vigging happens at the consensus layer, not per record.
        """
        return 1.0 / self.odds if self.odds and self.odds > 1.0 else float("nan")


@dataclass
class MatchContext:
    """Per-match metadata the measurement layer needs but OddsRecord lacks.

    kickoff is required to anchor the time-to-kickoff horizons. closing_ts, if
    provided, pins which snapshot is the official close; otherwise the last
    pre-kickoff snapshot is used as a *provisional* close (clearly flagged).
    """

    match_id: str
    kickoff: datetime
    closing_ts: Optional[datetime] = None
    label: str = ""

    def __post_init__(self) -> None:
        if self.kickoff.tzinfo is None:
            self.kickoff = self.kickoff.replace(tzinfo=timezone.utc)
        else:
            self.kickoff = self.kickoff.astimezone(timezone.utc)
        if self.closing_ts is not None and self.closing_ts.tzinfo is None:
            self.closing_ts = self.closing_ts.replace(tzinfo=timezone.utc)
