"""M4 — Append-only event store + lifecycle service (event sourcing).

Append-only, immutable audit trail with replay-based deterministic
reconstruction and crash recovery. Pure-stdlib (sqlite3), network-free.

The store records EVERY event (including duplicates) for a faithful audit trail;
idempotency/dedup is applied deterministically by the aggregate on fold/replay,
so reconstruction always yields the same counters.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Dict, List, Optional

from .lifecycle import Event, EventType, MatchLifecycle, ApplyResult


def _epoch(ts: datetime) -> float:
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(timezone.utc).timestamp()


class EventStore:
    """Append-only event log (SQLite)."""

    def __init__(self, db_path: str = ":memory:") -> None:
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS lifecycle_events (
                seq INTEGER PRIMARY KEY AUTOINCREMENT,
                match_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                idempotency_key TEXT NOT NULL,
                occurred_at_ts REAL NOT NULL,
                occurred_at_iso TEXT NOT NULL,
                recorded_at_iso TEXT NOT NULL,
                payload TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_events_match
                ON lifecycle_events(match_id, seq);
            """
        )
        self.conn.commit()

    def append(self, ev: Event) -> Event:
        """Append (immutably). Assigns seq + recorded_at; never updates/deletes."""
        recorded = datetime.now(timezone.utc)
        cur = self.conn.execute(
            "INSERT INTO lifecycle_events (match_id,event_type,idempotency_key,"
            "occurred_at_ts,occurred_at_iso,recorded_at_iso,payload) "
            "VALUES (?,?,?,?,?,?,?)",
            (ev.match_id, ev.type.value, ev.idempotency_key, _epoch(ev.occurred_at),
             ev.occurred_at.astimezone(timezone.utc).isoformat(),
             recorded.isoformat(), json.dumps(ev.payload)),
        )
        self.conn.commit()
        ev.seq = cur.lastrowid
        ev.recorded_at = recorded
        return ev

    def events_for(self, match_id: str) -> List[Event]:
        rows = self.conn.execute(
            "SELECT * FROM lifecycle_events WHERE match_id=? ORDER BY seq",
            (match_id,),
        ).fetchall()
        return [self._row_to_event(r) for r in rows]

    def all_match_ids(self) -> List[str]:
        rows = self.conn.execute(
            "SELECT DISTINCT match_id FROM lifecycle_events ORDER BY match_id"
        ).fetchall()
        return [r["match_id"] for r in rows]

    @staticmethod
    def _row_to_event(row: sqlite3.Row) -> Event:
        ev = Event(
            match_id=row["match_id"],
            type=EventType(row["event_type"]),
            idempotency_key=row["idempotency_key"],
            occurred_at=datetime.fromisoformat(row["occurred_at_iso"]),
            payload=json.loads(row["payload"]) if row["payload"] else {},
        )
        ev.seq = row["seq"]
        ev.recorded_at = datetime.fromisoformat(row["recorded_at_iso"])
        return ev

    def close(self) -> None:
        self.conn.close()


class LifecycleService:
    """Drives aggregates over an append-only store; rebuilds on demand.

    Recovery model:
    - process restart: a fresh service on the same store rebuilds every
      aggregate by replaying its events (deterministic).
    - duplicate events: deduped by idempotency_key on fold/replay.
    - out-of-order / delayed updates: events are folded in store (append) order;
      state guards reject events illegal for the current state (counted, not
      applied), so a late odds update after LOCKED cannot mutate state.
    """

    def __init__(self, store: Optional[EventStore] = None) -> None:
        self.store = store or EventStore()
        self._cache: Dict[str, MatchLifecycle] = {}

    def handle(self, ev: Event) -> ApplyResult:
        """Append then fold a single event into the (cached) aggregate."""
        self.store.append(ev)
        agg = self._cache.get(ev.match_id) or self.rebuild(ev.match_id, _exclude_last=ev)
        result = agg.apply(ev)
        self._cache[ev.match_id] = agg
        return result

    def get(self, match_id: str) -> MatchLifecycle:
        if match_id not in self._cache:
            self._cache[match_id] = self.rebuild(match_id)
        return self._cache[match_id]

    def rebuild(self, match_id: str, _exclude_last: Optional[Event] = None) -> MatchLifecycle:
        """Deterministically reconstruct an aggregate from the event log."""
        events = self.store.events_for(match_id)
        if _exclude_last is not None and events:
            # the just-appended event is folded by the caller; replay the rest
            events = events[:-1]
        return MatchLifecycle.replay(match_id, events)

    def snapshot(self, match_id: str, now: Optional[datetime] = None) -> dict:
        return self.get(match_id).snapshot(now)
