"""Task 5 — Data Integrity Layer.

Detects and flags structural problems in the snapshot stream *before* signals
are trusted. Pure validation; it never mutates or repairs data (R1.1 owns
normalization/dedup) — it only reports.

Checks
------
1. missing_snapshot_gaps   — expected pre-match horizons (24/12/6/1h) with no
                             snapshot at/before their cut-off, or a chosen
                             snapshot whose age deviates from target beyond
                             ``gap_tolerance_h``.
2. timestamp_irregularities — non-monotonic order after sort (duplicate stamps),
                             timezone-naive stamps, or stamps in the future.
3. duplicate_odds_sequences — >= ``dup_run`` consecutive records with identical
                             odds AND identical timestamp gaps (frozen feed), or
                             exact (timestamp, odds) repeats.
4. impossible_market_jumps  — |Δ implied_prob| between consecutive snapshots
                             exceeding ``max_prob_jump`` within
                             ``jump_window_h`` hours, or an odds ratio outside
                             [1/``max_odds_ratio``, ``max_odds_ratio``].
"""

from __future__ import annotations

from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone
from typing import Dict, List, Optional

from .schema import Horizon, MarketKey, PREMATCH_HORIZONS
from .timeseries import MarketTimeSeries


@dataclass
class IntegrityFlag:
    key_str: str
    check: str
    severity: str            # INFO | WARN | ERROR
    detail: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class IntegrityReport:
    flags: List[IntegrityFlag] = field(default_factory=list)
    counts: Dict[str, int] = field(default_factory=dict)
    series_checked: int = 0

    def add(self, flag: IntegrityFlag) -> None:
        self.flags.append(flag)
        self.counts[flag.check] = self.counts.get(flag.check, 0) + 1

    def to_dict(self) -> dict:
        return {
            "series_checked": self.series_checked,
            "total_flags": len(self.flags),
            "counts_by_check": self.counts,
            "flags": [f.to_dict() for f in self.flags],
        }


class DataIntegrityLayer:
    def __init__(
        self,
        gap_tolerance_h: float = 3.0,
        dup_run: int = 3,
        max_prob_jump: float = 0.25,
        jump_window_h: float = 2.0,
        max_odds_ratio: float = 2.5,
    ) -> None:
        self.gap_tolerance_h = gap_tolerance_h
        self.dup_run = dup_run
        self.max_prob_jump = max_prob_jump
        self.jump_window_h = jump_window_h
        self.max_odds_ratio = max_odds_ratio

    def validate(self, series_map: Dict[MarketKey, MarketTimeSeries]) -> IntegrityReport:
        report = IntegrityReport()
        now = datetime.now(timezone.utc)
        for key, series in series_map.items():
            report.series_checked += 1
            ks = self._key_str(key)
            self._check_gaps(series, ks, report)
            self._check_timestamps(series, ks, report, now)
            self._check_duplicates(series, ks, report)
            self._check_jumps(series, ks, report)
        return report

    # -- checks -------------------------------------------------------------
    def _check_gaps(self, series, ks, report):
        for h in PREMATCH_HORIZONS:
            pt = series.point(h)
            if pt is None or pt.record is None:
                report.add(IntegrityFlag(ks, "missing_snapshot_gaps", "WARN",
                                         f"no snapshot at/before {h.value} cutoff"))
            elif pt.gap_hours is not None and pt.gap_hours > self.gap_tolerance_h:
                report.add(IntegrityFlag(
                    ks, "missing_snapshot_gaps", "INFO",
                    f"{h.value} bucket filled by snapshot {pt.actual_age_hours:.1f}h "
                    f"before KO (target {pt.target_age_hours:.0f}h, "
                    f"gap {pt.gap_hours:.1f}h)"))

    def _check_timestamps(self, series, ks, report, now):
        prev = None
        for r in series.ordered:
            if r.timestamp.tzinfo is None:
                report.add(IntegrityFlag(ks, "timestamp_irregularities", "ERROR",
                                         "timezone-naive timestamp"))
            if r.timestamp > now:
                report.add(IntegrityFlag(ks, "timestamp_irregularities", "WARN",
                                         f"future timestamp {r.timestamp.isoformat()}"))
            if prev is not None and r.timestamp == prev:
                report.add(IntegrityFlag(ks, "timestamp_irregularities", "INFO",
                                         f"repeated timestamp {r.timestamp.isoformat()}"))
            prev = r.timestamp

    def _check_duplicates(self, series, ks, report):
        recs = series.ordered
        # exact (timestamp, odds) repeats
        seen = set()
        for r in recs:
            sig = (r.timestamp, round(r.odds, 6))
            if sig in seen:
                report.add(IntegrityFlag(ks, "duplicate_odds_sequences", "INFO",
                                         f"exact repeat odds={r.odds} @ {r.timestamp.isoformat()}"))
            seen.add(sig)
        # frozen run: dup_run consecutive identical odds
        run = 1
        for i in range(1, len(recs)):
            if abs(recs[i].odds - recs[i - 1].odds) < 1e-9:
                run += 1
                if run == self.dup_run:
                    report.add(IntegrityFlag(
                        ks, "duplicate_odds_sequences", "WARN",
                        f"{self.dup_run} consecutive identical odds={recs[i].odds} "
                        f"(possible frozen feed)"))
            else:
                run = 1

    def _check_jumps(self, series, ks, report):
        recs = series.ordered
        for i in range(1, len(recs)):
            a, b = recs[i - 1], recs[i]
            if a.odds <= 1.0 or b.odds <= 1.0:
                continue
            dt_h = (b.timestamp - a.timestamp).total_seconds() / 3600.0
            dp = abs((1.0 / b.odds) - (1.0 / a.odds))
            ratio = b.odds / a.odds
            if dt_h <= self.jump_window_h and dp > self.max_prob_jump:
                report.add(IntegrityFlag(
                    ks, "impossible_market_jumps", "ERROR",
                    f"Δprob={dp:.3f} in {dt_h:.2f}h ({a.odds}->{b.odds})"))
            elif ratio > self.max_odds_ratio or ratio < 1.0 / self.max_odds_ratio:
                report.add(IntegrityFlag(
                    ks, "impossible_market_jumps", "WARN",
                    f"odds ratio {ratio:.2f} ({a.odds}->{b.odds})"))

    @staticmethod
    def _key_str(key: MarketKey) -> str:
        bk = f"@{key.bookmaker}" if key.bookmaker else "@consensus"
        return f"{key.match_id}|{key.market}|{key.selection}{bk}"
