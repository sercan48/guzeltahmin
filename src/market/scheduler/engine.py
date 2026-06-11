"""M10.1 — Deterministic Scheduler & Snapshot Engine.

Generates the T-72h..CLOSE snapshot schedule from a kickoff, queues the events
in an idempotent, replay-safe trigger queue, tracks completeness, and exposes
monitoring. Pure-stdlib (sqlite3), NO network, no ML/prediction/betting.

Determinism: time is read only via an injectable Clock; the same kickoff always
yields an identical schedule; queue/completeness rebuild deterministically from
the append-only logs.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

import sqlite3

from .clock import Clock, ManualClock

# snapshot schedule: hours-before-kickoff -> tick label
SCHEDULE_TICKS: List[Tuple[float, str]] = [
    (72.0, "T-72h"), (48.0, "T-48h"), (24.0, "T-24h"), (12.0, "T-12h"),
    (6.0, "T-6h"), (1.0, "T-1h"), (0.0, "CLOSE"),
]

# completeness weights (CLOSE weighted higher — it is the most valuable snapshot)
DEFAULT_TICK_WEIGHTS: Dict[str, float] = {t: (2.0 if t == "CLOSE" else 1.0)
                                          for _, t in SCHEDULE_TICKS}


def _epoch(t: datetime) -> float:
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    return t.astimezone(timezone.utc).timestamp()


def _iso(t: datetime) -> str:
    return t.astimezone(timezone.utc).isoformat()


@dataclass(frozen=True)
class ScheduledEvent:
    event_id: str          # stable: "<match>:<tick>"
    match_id: str
    tick: str
    hours_before_ko: float
    scheduled_at: str      # ISO UTC

    def trigger_type(self) -> str:
        """Map a tick to the M5 trigger semantics (string; no M5 coupling)."""
        return "MATCH_STARTED" if self.tick == "CLOSE" else "ODDS_UPDATED"


def generate_schedule(match_id: str, kickoff: datetime,
                      ticks: List[Tuple[float, str]] = SCHEDULE_TICKS
                      ) -> List[ScheduledEvent]:
    """Deterministic schedule from a kickoff. Stable ids, ordered, no duplicates."""
    if kickoff.tzinfo is None:
        kickoff = kickoff.replace(tzinfo=timezone.utc)
    seen = set()
    out: List[ScheduledEvent] = []
    for hours, tick in ticks:
        eid = f"{match_id}:{tick}"
        if eid in seen:
            continue
        seen.add(eid)
        out.append(ScheduledEvent(eid, match_id, tick, hours,
                                  _iso(kickoff - timedelta(hours=hours))))
    out.sort(key=lambda e: (e.scheduled_at, e.event_id))
    return out


class SnapshotScheduler:
    """Schedule + idempotent trigger queue + completeness, over append-only logs."""

    def __init__(self, clock: Optional[Clock] = None, db_path: str = ":memory:",
                 tick_weights: Optional[Dict[str, float]] = None) -> None:
        self.clock = clock or ManualClock(datetime(2026, 1, 1, tzinfo=timezone.utc))
        self.weights = dict(tick_weights or DEFAULT_TICK_WEIGHTS)
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS schedule (
                event_id TEXT PRIMARY KEY, match_id TEXT, tick TEXT,
                hours_before REAL, scheduled_at_ts REAL, scheduled_at_iso TEXT
            );
            CREATE TABLE IF NOT EXISTS dispatch (
                event_id TEXT PRIMARY KEY, dispatched_at_ts REAL, dispatched_at_iso TEXT
            );
            CREATE TABLE IF NOT EXISTS observation (
                event_id TEXT PRIMARY KEY, observed_at_ts REAL, observed_at_iso TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_sched_match ON schedule(match_id);
            """
        )
        self.conn.commit()

    # -- scheduling (idempotent) -------------------------------------------
    def schedule_match(self, match_id: str, kickoff: datetime) -> List[ScheduledEvent]:
        events = generate_schedule(match_id, kickoff)
        for e in events:
            self.conn.execute(
                "INSERT OR IGNORE INTO schedule (event_id,match_id,tick,hours_before,"
                "scheduled_at_ts,scheduled_at_iso) VALUES (?,?,?,?,?,?)",
                (e.event_id, e.match_id, e.tick, e.hours_before_ko,
                 _epoch(datetime.fromisoformat(e.scheduled_at)), e.scheduled_at),
            )
        self.conn.commit()
        return events

    # -- trigger queue ------------------------------------------------------
    def due(self) -> List[ScheduledEvent]:
        """Return + dispatch all pending events with scheduled_at <= now.

        Deterministic order; idempotent — a dispatched event is never returned
        again (processing-once)."""
        now_ts = _epoch(self.clock.now())
        rows = self.conn.execute(
            "SELECT s.* FROM schedule s LEFT JOIN dispatch d ON s.event_id=d.event_id "
            "WHERE d.event_id IS NULL AND s.scheduled_at_ts <= ? "
            "ORDER BY s.scheduled_at_ts, s.event_id", (now_ts,),
        ).fetchall()
        out = []
        for r in rows:
            self.conn.execute(
                "INSERT OR IGNORE INTO dispatch (event_id,dispatched_at_ts,dispatched_at_iso) "
                "VALUES (?,?,?)", (r["event_id"], now_ts, _iso(self.clock.now())),
            )
            out.append(self._row_to_event(r))
        self.conn.commit()
        return out

    def observe(self, event_id: str, at: Optional[datetime] = None) -> bool:
        """Record that the scheduled snapshot was actually captured. Idempotent."""
        if not self.conn.execute("SELECT 1 FROM schedule WHERE event_id=?",
                                 (event_id,)).fetchone():
            return False
        t = at or self.clock.now()
        self.conn.execute(
            "INSERT OR IGNORE INTO observation (event_id,observed_at_ts,observed_at_iso) "
            "VALUES (?,?,?)", (event_id, _epoch(t), _iso(t)),
        )
        self.conn.commit()
        return True

    # -- completeness -------------------------------------------------------
    def completeness(self, match_id: str) -> float:
        rows = self.conn.execute(
            "SELECT s.tick, o.event_id AS obs FROM schedule s "
            "LEFT JOIN observation o ON s.event_id=o.event_id WHERE s.match_id=?",
            (match_id,),
        ).fetchall()
        if not rows:
            return 0.0
        expected = sum(self.weights.get(r["tick"], 1.0) for r in rows)
        observed = sum(self.weights.get(r["tick"], 1.0) for r in rows if r["obs"])
        return round(observed / expected, 6) if expected else 0.0

    # -- monitoring ---------------------------------------------------------
    def next_trigger(self) -> Optional[ScheduledEvent]:
        r = self.conn.execute(
            "SELECT s.* FROM schedule s LEFT JOIN dispatch d ON s.event_id=d.event_id "
            "WHERE d.event_id IS NULL ORDER BY s.scheduled_at_ts, s.event_id LIMIT 1"
        ).fetchone()
        return self._row_to_event(r) if r else None

    def missed_snapshots(self, grace_seconds: float = 3600.0) -> List[str]:
        cutoff = _epoch(self.clock.now()) - grace_seconds
        rows = self.conn.execute(
            "SELECT s.event_id FROM schedule s LEFT JOIN observation o ON s.event_id=o.event_id "
            "WHERE o.event_id IS NULL AND s.scheduled_at_ts < ? ORDER BY s.event_id", (cutoff,),
        ).fetchall()
        return [r["event_id"] for r in rows]

    def delayed_snapshots(self, tolerance_seconds: float = 300.0) -> List[Tuple[str, float]]:
        rows = self.conn.execute(
            "SELECT s.event_id, (d.dispatched_at_ts - s.scheduled_at_ts) AS lag "
            "FROM schedule s JOIN dispatch d ON s.event_id=d.event_id "
            "WHERE (d.dispatched_at_ts - s.scheduled_at_ts) > ? ORDER BY s.event_id",
            (tolerance_seconds,),
        ).fetchall()
        return [(r["event_id"], round(r["lag"], 3)) for r in rows]

    # -- replay -------------------------------------------------------------
    def replay(self) -> dict:
        """Deterministic reconstruction of queue + completeness from the logs."""
        matches = [r["match_id"] for r in self.conn.execute(
            "SELECT DISTINCT match_id FROM schedule ORDER BY match_id").fetchall()]
        nxt = self.next_trigger()
        return {
            "n_scheduled": self._count("schedule"),
            "n_dispatched": self._count("dispatch"),
            "n_observed": self._count("observation"),
            "completeness": {m: self.completeness(m) for m in matches},
            "next_trigger": nxt.event_id if nxt else None,
        }

    # -- internals ----------------------------------------------------------
    def _count(self, table: str) -> int:
        return self.conn.execute(f"SELECT COUNT(*) c FROM {table}").fetchone()["c"]

    @staticmethod
    def _row_to_event(r: sqlite3.Row) -> ScheduledEvent:
        return ScheduledEvent(r["event_id"], r["match_id"], r["tick"],
                              r["hours_before"], r["scheduled_at_iso"])

    def close(self) -> None:
        self.conn.close()
