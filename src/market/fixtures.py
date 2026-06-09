"""Deterministic 5-match snapshot fixture for R1.2 measurement validation.

No network, no randomness — the same input every run, so the report embedded in
the docs is reproducible. Snapshots span OPEN(T-48h) .. CLOSE(T-0) across three
bookmakers (pinnacle/bet365 = sharp, conf 0.95; obscurebet = soft, conf 0.50).

Intentional data-quality defects (to exercise the integrity layer):
- evt_juv_mil : pinnacle 6h bucket missing (gap) + obscurebet frozen feed
- evt_bay_dor : bet365 impossible HOME jump + a future-dated stamp + exact dup
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Dict, List, Tuple

from .schema import OddsRecord, MatchContext, SnapshotType

# Kickoff is anchored a few hours in the *past* relative to run time so the
# OPEN..CLOSE grid is genuinely historical (no spurious future-timestamp
# flags). Signal values are time-relative, hence fully reproducible; only the
# absolute timestamp strings shift with run time. The single intentional
# future-dated stamp (year 2030, injected below) is the only future flag.
KO = (
    datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    - timedelta(hours=3)
)

# (hours_before_ko, snapshot_type)
GRID: List[Tuple[float, str]] = [
    (48.0, SnapshotType.OPEN.value),
    (24.0, SnapshotType.T24H.value),
    (12.0, SnapshotType.T12H.value),
    (6.0, SnapshotType.T6H.value),
    (1.0, SnapshotType.T1H.value),
    (0.0, SnapshotType.CLOSE.value),
]

# Per match -> per book -> per snapshot index -> (H, D, A) decimal odds.
# Index aligns with GRID order above.
_LINES: Dict[str, Dict[str, List[Tuple[float, float, float]]]] = {
    # 1) Arsenal-Chelsea: clear HOME shortening (sharp backing the home side)
    "evt_ars_che": {
        "pinnacle":   [(2.40, 3.30, 3.10), (2.20, 3.35, 3.30), (2.10, 3.40, 3.45),
                       (2.05, 3.45, 3.55), (2.00, 3.50, 3.60), (1.95, 3.55, 3.70)],
        "bet365":     [(2.38, 3.25, 3.15), (2.22, 3.30, 3.28), (2.12, 3.38, 3.42),
                       (2.06, 3.44, 3.52), (2.01, 3.48, 3.58), (1.96, 3.52, 3.66)],
        "obscurebet": [(2.45, 3.10, 3.00), (2.30, 3.15, 3.10), (2.20, 3.20, 3.20),
                       (2.15, 3.25, 3.25), (2.10, 3.30, 3.30), (2.08, 3.30, 3.35)],
    },
    # 2) Liverpool-ManCity: HOME drifting out (money on the away side)
    "evt_liv_mci": {
        "pinnacle":   [(2.05, 3.50, 3.55), (2.15, 3.45, 3.35), (2.25, 3.40, 3.20),
                       (2.35, 3.35, 3.05), (2.45, 3.30, 2.95), (2.55, 3.25, 2.85)],
        "bet365":     [(2.04, 3.48, 3.58), (2.16, 3.44, 3.34), (2.26, 3.40, 3.18),
                       (2.36, 3.34, 3.04), (2.46, 3.28, 2.94), (2.56, 3.22, 2.84)],
        "obscurebet": [(2.10, 3.30, 3.40), (2.20, 3.25, 3.25), (2.30, 3.20, 3.15),
                       (2.40, 3.15, 3.00), (2.50, 3.10, 2.90), (2.58, 3.08, 2.82)],
    },
    # 3) Barca-Real: tight market, high consensus, minimal drift
    "evt_bar_rma": {
        "pinnacle":   [(2.50, 3.40, 2.70), (2.48, 3.40, 2.72), (2.50, 3.38, 2.70),
                       (2.49, 3.40, 2.71), (2.50, 3.40, 2.70), (2.50, 3.40, 2.70)],
        "bet365":     [(2.51, 3.38, 2.69), (2.49, 3.39, 2.71), (2.50, 3.39, 2.70),
                       (2.50, 3.40, 2.70), (2.50, 3.39, 2.71), (2.51, 3.39, 2.70)],
        "obscurebet": [(2.55, 3.25, 2.65), (2.52, 3.30, 2.68), (2.54, 3.28, 2.66),
                       (2.53, 3.30, 2.67), (2.55, 3.28, 2.66), (2.55, 3.30, 2.65)],
    },
    # 4) Juventus-Milan: pinnacle 6h missing (gap), obscurebet frozen feed
    "evt_juv_mil": {
        "pinnacle":   [(1.90, 3.60, 4.20), (1.85, 3.65, 4.40), (1.82, 3.70, 4.55),
                       None,                (1.78, 3.75, 4.75), (1.75, 3.80, 4.90)],
        "bet365":     [(1.92, 3.55, 4.10), (1.86, 3.62, 4.35), (1.83, 3.68, 4.50),
                       (1.80, 3.72, 4.65), (1.79, 3.74, 4.72), (1.76, 3.78, 4.85)],
        # obscurebet frozen: same odds repeated across 12h/6h/1h
        "obscurebet": [(1.95, 3.40, 3.95), (1.90, 3.45, 4.10), (1.88, 3.50, 4.20),
                       (1.88, 3.50, 4.20), (1.88, 3.50, 4.20), (1.85, 3.55, 4.30)],
    },
    # 5) Bayern-Dortmund: bet365 impossible jump + future stamp + duplicate
    "evt_bay_dor": {
        "pinnacle":   [(1.50, 4.20, 6.50), (1.48, 4.30, 6.80), (1.47, 4.35, 7.00),
                       (1.46, 4.40, 7.10), (1.45, 4.45, 7.20), (1.44, 4.50, 7.30)],
        # bet365 HOME jumps 1.50 -> 4.50 at the 12h mark (impossible)
        "bet365":     [(1.50, 4.15, 6.40), (1.49, 4.25, 6.70), (4.50, 4.30, 6.90),
                       (1.46, 4.38, 7.05), (1.45, 4.42, 7.15), (1.44, 4.48, 7.25)],
        "obscurebet": [(1.55, 4.00, 6.00), (1.52, 4.10, 6.30), (1.50, 4.15, 6.50),
                       (1.49, 4.20, 6.60), (1.48, 4.25, 6.70), (1.47, 4.30, 6.80)],
    },
}

_LABELS = {
    "evt_ars_che": "Arsenal vs Chelsea",
    "evt_liv_mci": "Liverpool vs Man City",
    "evt_bar_rma": "Barcelona vs Real Madrid",
    "evt_juv_mil": "Juventus vs Milan",
    "evt_bay_dor": "Bayern vs Dortmund",
}

_BOOK_CONF = {"pinnacle": 0.95, "bet365": 0.95, "obscurebet": 0.50}
_SELS = ("HOME", "DRAW", "AWAY")


def build_fixture() -> Tuple[List[OddsRecord], Dict[str, MatchContext]]:
    records: List[OddsRecord] = []
    contexts: Dict[str, MatchContext] = {}

    for match_id, books in _LINES.items():
        contexts[match_id] = MatchContext(
            match_id=match_id, kickoff=KO, label=_LABELS[match_id]
        )
        for bk, grid in books.items():
            conf = _BOOK_CONF[bk]
            for snap_idx, prices in enumerate(grid):
                if prices is None:
                    continue  # intentional gap
                hours_before, snap_type = GRID[snap_idx]
                ts = KO - timedelta(hours=hours_before)
                for sel, odd in zip(_SELS, prices):
                    records.append(OddsRecord(
                        match_id=match_id, bookmaker=bk, market="1X2",
                        selection=sel, odds=odd, timestamp=ts,
                        snapshot_type=snap_type, source_id="fixture",
                        confidence_score=conf,
                    ))

    # --- inject a future-dated stamp + an exact duplicate on evt_bay_dor ----
    future_ts = datetime(2030, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    records.append(OddsRecord(
        match_id="evt_bay_dor", bookmaker="obscurebet", market="1X2",
        selection="HOME", odds=1.55, timestamp=future_ts,
        snapshot_type="LIVE", source_id="fixture", confidence_score=0.50,
    ))
    # exact duplicate of the bet365 open HOME quote
    records.append(OddsRecord(
        match_id="evt_bay_dor", bookmaker="bet365", market="1X2",
        selection="HOME", odds=1.50, timestamp=KO - timedelta(hours=48.0),
        snapshot_type=SnapshotType.OPEN.value, source_id="fixture",
        confidence_score=0.95,
    ))
    return records, contexts
