"""M8.2 — Closing Truth Lock & Immutable Closure Layer.

At kickoff, capture the canonical closing line from the M2 Truth Store and LOCK
it immutably into an append-only, hash-chained closure ledger. After the lock the
close cannot change: late provider updates (truth dated after kickoff) are
excluded by a strict point-in-time read, and re-locking is idempotent.

Additive: reads M2's TruthStore, writes its own ledger. No M1-M8.1 changes, no
ML, no betting logic, no provider redesign.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from enum import Enum
from typing import Dict, List, Optional

from ..truth import TruthStore, TruthRecord

_GENESIS = "GENESIS"


class CloseKind(str, Enum):
    OBSERVED_CLOSE = "OBSERVED_CLOSE"   # a real CLOSE-snapshot truth was locked
    FALLBACK = "FALLBACK"               # no CLOSE snapshot; latest pre-KO truth locked
    MISSING = "MISSING"                 # no truth at/before kickoff


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _epoch(ts: datetime) -> float:
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(timezone.utc).timestamp()


def _hash(prev_hash: str, payload: dict) -> str:
    body = json.dumps(payload, sort_keys=True)
    return hashlib.sha256(f"{prev_hash}|{body}".encode("utf-8")).hexdigest()


@dataclass
class ClosureRecord:
    match_id: str
    market: str
    selection: str
    o_close: Optional[float]
    p_close: Optional[float]
    provenance: Optional[str]
    confidence: Optional[float]
    close_as_of: Optional[str]          # point-in-time of the locked snapshot
    close_kind: str
    is_stale: bool
    source_composition: Dict[str, float]
    locked_at: str
    prev_hash: str
    entry_hash: str
    seq: Optional[int] = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ClosureSummary:
    n_locks: int                        # distinct (match, market) locked
    n_records: int
    n_missing: int
    n_stale: int
    mean_close_confidence: Optional[float]
    chain_valid: bool

    def to_dict(self) -> dict:
        return asdict(self)


class ClosureLedger:
    """SQLite append-only, hash-chained immutable closing-line ledger."""

    def __init__(self, db_path: str = ":memory:", stale_threshold_h: float = 6.0) -> None:
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
        self.stale_threshold_h = stale_threshold_h
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS closure_ledger (
                seq INTEGER PRIMARY KEY AUTOINCREMENT,
                lock_key TEXT, match_id TEXT, market TEXT, selection TEXT,
                o_close REAL, p_close REAL, provenance TEXT, confidence REAL,
                close_as_of_iso TEXT, close_kind TEXT, is_stale INTEGER,
                source_composition TEXT, locked_at_iso TEXT, prev_hash TEXT, entry_hash TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_closure_lock ON closure_ledger(match_id, market);
            """
        )
        self.conn.commit()

    # -- locking (idempotent, point-in-time) -------------------------------
    def is_locked(self, match_id: str, market: str) -> bool:
        return self.conn.execute(
            "SELECT 1 FROM closure_ledger WHERE match_id=? AND market=? LIMIT 1",
            (match_id, market),
        ).fetchone() is not None

    def lock(self, store: TruthStore, match_id: str, market: str,
             kickoff: datetime) -> List[ClosureRecord]:
        """Kickoff-triggered immutable lock of the canonical close.

        Point-in-time: only truth with as_of <= kickoff is eligible, so any late
        update cannot enter the close. Idempotent: re-locking returns the
        existing records without writing or changing anything.
        """
        if self.is_locked(match_id, market):
            return self.get_closes(match_id, market)        # duplicate protection

        # eligible truth = strictly at/before kickoff (excludes late updates)
        truth = store.iter_truth(match_id=match_id, market=market, as_of=kickoff)
        chosen = self._pick_close_per_selection(truth, kickoff)

        records: List[ClosureRecord] = []
        if not chosen:
            records.append(self._persist(match_id, market, "*", None, kickoff,
                                         CloseKind.MISSING, True, {}))
            return records
        for sel, (rec, kind) in chosen.items():
            age_h = (kickoff - datetime.fromisoformat(rec.as_of)).total_seconds() / 3600.0
            records.append(self._persist(match_id, market, sel, rec, kickoff,
                                         kind, age_h > self.stale_threshold_h,
                                         rec.contributing_providers))
        return records

    def _pick_close_per_selection(self, truth: List[TruthRecord], kickoff: datetime):
        """Per selection: prefer the CLOSE snapshot, else the latest pre-KO truth."""
        by_sel: Dict[str, List[TruthRecord]] = {}
        for r in truth:
            by_sel.setdefault(r.selection, []).append(r)
        chosen: Dict[str, tuple] = {}
        for sel, recs in by_sel.items():
            close_recs = [r for r in recs if r.snapshot_type == "CLOSE"]
            if close_recs:
                best = max(close_recs, key=lambda r: r.as_of)
                chosen[sel] = (best, CloseKind.OBSERVED_CLOSE)
            else:
                best = max(recs, key=lambda r: r.as_of)
                chosen[sel] = (best, CloseKind.FALLBACK)
        return chosen

    # -- read ---------------------------------------------------------------
    def get_closes(self, match_id: str, market: str) -> List[ClosureRecord]:
        rows = self.conn.execute(
            "SELECT * FROM closure_ledger WHERE match_id=? AND market=? ORDER BY seq",
            (match_id, market),
        ).fetchall()
        return [self._row_to_record(r) for r in rows]

    def get_close(self, match_id: str, market: str, selection: str) -> Optional[ClosureRecord]:
        row = self.conn.execute(
            "SELECT * FROM closure_ledger WHERE match_id=? AND market=? AND selection=? "
            "ORDER BY seq LIMIT 1", (match_id, market, selection),
        ).fetchone()
        return self._row_to_record(row) if row else None

    # -- replay / integrity -------------------------------------------------
    def replay(self) -> ClosureSummary:
        rows = self.conn.execute("SELECT * FROM closure_ledger ORDER BY seq").fetchall()
        locks = {(r["match_id"], r["market"]) for r in rows}
        confs = [r["confidence"] for r in rows if r["confidence"] is not None]
        return ClosureSummary(
            n_locks=len(locks), n_records=len(rows),
            n_missing=sum(1 for r in rows if r["close_kind"] == CloseKind.MISSING.value),
            n_stale=sum(1 for r in rows if r["is_stale"]),
            mean_close_confidence=round(sum(confs) / len(confs), 6) if confs else None,
            chain_valid=self.verify_chain(),
        )

    def verify_chain(self) -> bool:
        rows = self.conn.execute("SELECT * FROM closure_ledger ORDER BY seq").fetchall()
        prev = _GENESIS
        for r in rows:
            if r["prev_hash"] != prev or r["entry_hash"] != _hash(prev, self._payload(r)):
                return False
            prev = r["entry_hash"]
        return True

    # -- internals ----------------------------------------------------------
    def _persist(self, match_id, market, selection, rec: Optional[TruthRecord],
                 kickoff, kind: CloseKind, is_stale: bool,
                 source_comp: Dict[str, float]) -> ClosureRecord:
        o_close = rec.o_truth if rec else None
        p_close = rec.p_truth if rec else None
        provenance = rec.provenance if rec else None
        confidence = rec.confidence if rec else None
        close_as_of = rec.as_of if rec else None
        prev_hash = self._last_hash()
        payload = self._payload_fields(match_id, market, selection, o_close, p_close,
                                       provenance, confidence, close_as_of, kind.value,
                                       is_stale, source_comp)
        entry_hash = _hash(prev_hash, payload)
        locked_at = _now_iso()
        self.conn.execute(
            "INSERT INTO closure_ledger (lock_key,match_id,market,selection,o_close,"
            "p_close,provenance,confidence,close_as_of_iso,close_kind,is_stale,"
            "source_composition,locked_at_iso,prev_hash,entry_hash) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (f"{match_id}:{market}", match_id, market, selection, o_close, p_close,
             provenance, confidence, close_as_of, kind.value, 1 if is_stale else 0,
             json.dumps(source_comp), locked_at, prev_hash, entry_hash),
        )
        self.conn.commit()
        seq = self.conn.execute("SELECT MAX(seq) s FROM closure_ledger").fetchone()["s"]
        out = ClosureRecord(match_id, market, selection, o_close, p_close, provenance,
                            confidence, close_as_of, kind.value, is_stale, source_comp,
                            locked_at, prev_hash, entry_hash)
        out.seq = seq
        return out

    @staticmethod
    def _payload_fields(match_id, market, selection, o_close, p_close, provenance,
                        confidence, close_as_of, close_kind, is_stale, source_comp) -> dict:
        return {
            "match_id": match_id, "market": market, "selection": selection,
            "o_close": None if o_close is None else round(o_close, 6),
            "p_close": None if p_close is None else round(p_close, 6),
            "provenance": provenance,
            "confidence": None if confidence is None else round(confidence, 6),
            "close_as_of": close_as_of, "close_kind": close_kind,
            "is_stale": bool(is_stale),
            "source_composition": json.dumps(source_comp, sort_keys=True),
        }

    def _payload(self, row: sqlite3.Row) -> dict:
        return self._payload_fields(
            row["match_id"], row["market"], row["selection"], row["o_close"],
            row["p_close"], row["provenance"], row["confidence"], row["close_as_of_iso"],
            row["close_kind"], bool(row["is_stale"]),
            json.loads(row["source_composition"]),
        )

    def _last_hash(self) -> str:
        row = self.conn.execute(
            "SELECT entry_hash FROM closure_ledger ORDER BY seq DESC LIMIT 1"
        ).fetchone()
        return row["entry_hash"] if row else _GENESIS

    @staticmethod
    def _row_to_record(row: sqlite3.Row) -> ClosureRecord:
        rec = ClosureRecord(
            match_id=row["match_id"], market=row["market"], selection=row["selection"],
            o_close=row["o_close"], p_close=row["p_close"], provenance=row["provenance"],
            confidence=row["confidence"], close_as_of=row["close_as_of_iso"],
            close_kind=row["close_kind"], is_stale=bool(row["is_stale"]),
            source_composition=json.loads(row["source_composition"]),
            locked_at=row["locked_at_iso"], prev_hash=row["prev_hash"],
            entry_hash=row["entry_hash"],
        )
        rec.seq = row["seq"]
        return rec

    def close(self) -> None:
        self.conn.close()
