"""DelayScheduler — append-only SQLite queue for time-delayed signal delivery.

Schema (delay_queue.db):
  delay_queue — one row per (user, signal) delivery slot

All timestamps are Unix floats sourced from the injected Clock.
No wall-clock calls. Deterministic replay: re-open DB → same due() results.

Unique constraint on (signal_id, user_id) prevents duplicate enqueues for
the same signal/user combination across restarts.
"""

from __future__ import annotations

import sqlite3
from typing import List, Optional

from .clock import Clock
from .models import QueueEntry


_DDL = """
CREATE TABLE IF NOT EXISTS delay_queue (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_id        TEXT NOT NULL,
    user_id          TEXT NOT NULL,
    user_tier        TEXT NOT NULL,
    channel_id       TEXT NOT NULL,
    format_type      TEXT NOT NULL,
    signal_json      TEXT NOT NULL,
    gate_json        TEXT NOT NULL,
    publish_after_ts REAL NOT NULL,
    created_at_ts    REAL NOT NULL,
    delivered_at_ts  REAL,
    UNIQUE(signal_id, user_id)
);
CREATE INDEX IF NOT EXISTS idx_due
    ON delay_queue(publish_after_ts, delivered_at_ts);
"""


class DelayScheduler:
    """Append-only delivery queue with deterministic due() semantics."""

    def __init__(self, db_path: str, clock: Clock) -> None:
        self._clock = clock
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        for stmt in _DDL.strip().split(";"):
            stmt = stmt.strip()
            if stmt:
                self._conn.execute(stmt)
        self._conn.commit()

    # ------------------------------------------------------------------ #

    def enqueue(
        self,
        signal_id: str,
        user_id: str,
        user_tier: str,
        channel_id: str,
        format_type: str,
        signal_json: str,
        gate_json: str,
        publish_after_ts: float,
    ) -> Optional[int]:
        """Insert row; returns queue_id (rowid), or None if already enqueued."""
        try:
            with self._conn:
                cur = self._conn.execute(
                    """INSERT INTO delay_queue
                       (signal_id, user_id, user_tier, channel_id, format_type,
                        signal_json, gate_json, publish_after_ts, created_at_ts)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        signal_id, user_id, user_tier, channel_id,
                        format_type, signal_json, gate_json,
                        publish_after_ts, self._clock.now_ts(),
                    ),
                )
                return cur.lastrowid
        except sqlite3.IntegrityError:
            # Duplicate (signal_id, user_id) — idempotent, safe to ignore
            return None

    def due(self, now_ts: float) -> List[QueueEntry]:
        """Return all undelivered entries with publish_after_ts ≤ now_ts."""
        rows = self._conn.execute(
            """SELECT * FROM delay_queue
               WHERE delivered_at_ts IS NULL AND publish_after_ts <= ?
               ORDER BY publish_after_ts ASC, id ASC""",
            (now_ts,),
        ).fetchall()
        return [_row_to_entry(r) for r in rows]

    def mark_delivered(self, queue_id: int, now_ts: float) -> None:
        """Set delivered_at_ts. Row is never deleted (append-only)."""
        with self._conn:
            self._conn.execute(
                "UPDATE delay_queue SET delivered_at_ts=? WHERE id=?",
                (now_ts, queue_id),
            )

    def pending_count(self) -> int:
        row = self._conn.execute(
            "SELECT COUNT(*) FROM delay_queue WHERE delivered_at_ts IS NULL"
        ).fetchone()
        return row[0]

    def get_entry(self, queue_id: int) -> Optional[QueueEntry]:
        row = self._conn.execute(
            "SELECT * FROM delay_queue WHERE id=?", (queue_id,)
        ).fetchone()
        return _row_to_entry(row) if row else None

    def close(self) -> None:
        self._conn.close()


# ---------------------------------------------------------------------------

def _row_to_entry(row: sqlite3.Row) -> QueueEntry:
    return QueueEntry(
        queue_id=row["id"],
        signal_id=row["signal_id"],
        user_id=row["user_id"],
        user_tier=row["user_tier"],
        channel_id=row["channel_id"],
        format_type=row["format_type"],
        signal_json=row["signal_json"],
        gate_json=row["gate_json"],
        publish_after_ts=row["publish_after_ts"],
        created_at_ts=row["created_at_ts"],
        delivered_at_ts=row["delivered_at_ts"],
    )
