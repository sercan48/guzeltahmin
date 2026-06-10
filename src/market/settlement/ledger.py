"""M8.1 — Outcome ingestion + settlement ledger (SQLite).

Append-only, hash-chained, idempotent, replay-safe. Pure-stdlib, no network, no
ML, no betting/stake logic (paper flat-unit ROI only — no Kelly, no sizing).

Realized metrics:
    ROI_realized = WON: o_entry - 1 ; LOST: -1 ; PUSH/VOID: 0     (flat unit stake)
    CLV_realized = o_entry / o_close - 1   (when closing odds available; price metric)
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

from .outcomes import MatchOutcome, OutcomeStatus, SettlementResult, resolve_market

_GENESIS = "GENESIS"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class SettlementRecord:
    settlement_id: str
    match_id: str
    market: str
    selection: str
    entry_odds: float
    closing_odds: Optional[float]
    result: str
    realized_roi: float
    realized_clv: Optional[float]
    is_void: bool
    settled_at: str
    prev_hash: str
    entry_hash: str
    seq: Optional[int] = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class SettlementSummary:
    n_settlements: int
    n_void: int
    total_roi: float
    mean_roi: float
    mean_clv: Optional[float]
    beat_close_rate: Optional[float]
    chain_valid: bool
    per_match: Dict[str, float]

    def to_dict(self) -> dict:
        return asdict(self)


def _roi_for(result: SettlementResult, entry_odds: float) -> float:
    if result == SettlementResult.WON:
        return entry_odds - 1.0
    if result == SettlementResult.LOST:
        return -1.0
    return 0.0   # PUSH / VOID -> stake refunded


def _clv_for(entry_odds: float, closing_odds: Optional[float]) -> Optional[float]:
    if closing_odds is None or closing_odds <= 0:
        return None
    return entry_odds / closing_odds - 1.0


def _hash(prev_hash: str, payload: dict) -> str:
    body = json.dumps(payload, sort_keys=True)
    return hashlib.sha256(f"{prev_hash}|{body}".encode("utf-8")).hexdigest()


class SettlementLedger:
    """SQLite-backed outcome store + append-only hash-chained settlement ledger."""

    def __init__(self, db_path: str = ":memory:") -> None:
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS match_outcomes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                match_id TEXT, source TEXT, status TEXT,
                home_goals INTEGER, away_goals INTEGER,
                ingested_at_iso TEXT, idempotency_key TEXT UNIQUE
            );
            CREATE TABLE IF NOT EXISTS settlement_ledger (
                seq INTEGER PRIMARY KEY AUTOINCREMENT,
                settlement_id TEXT UNIQUE,
                match_id TEXT, market TEXT, selection TEXT,
                entry_odds REAL, closing_odds REAL,
                result TEXT, realized_roi REAL, realized_clv REAL, is_void INTEGER,
                settled_at_iso TEXT, prev_hash TEXT, entry_hash TEXT
            );
            """
        )
        self.conn.commit()

    # -- outcome ingestion (idempotent) ------------------------------------
    def ingest_outcome(self, outcome: MatchOutcome) -> Tuple[MatchOutcome, bool]:
        """Idempotent by (match_id, source). Returns (outcome, is_duplicate)."""
        key = f"{outcome.match_id}:{outcome.source}"
        existing = self.conn.execute(
            "SELECT 1 FROM match_outcomes WHERE idempotency_key=?", (key,)
        ).fetchone()
        if existing:
            return outcome, True
        self.conn.execute(
            "INSERT INTO match_outcomes (match_id,source,status,home_goals,away_goals,"
            "ingested_at_iso,idempotency_key) VALUES (?,?,?,?,?,?,?)",
            (outcome.match_id, outcome.source, outcome.status.value,
             outcome.home_goals, outcome.away_goals,
             outcome.ingested_at.astimezone(timezone.utc).isoformat(), key),
        )
        self.conn.commit()
        return outcome, False

    def get_outcome(self, match_id: str) -> Optional[MatchOutcome]:
        """Canonical outcome for a match (latest ingested)."""
        row = self.conn.execute(
            "SELECT * FROM match_outcomes WHERE match_id=? ORDER BY id DESC LIMIT 1",
            (match_id,),
        ).fetchone()
        if not row:
            return None
        return MatchOutcome(
            match_id=row["match_id"], status=OutcomeStatus(row["status"]),
            home_goals=row["home_goals"], away_goals=row["away_goals"],
            source=row["source"], ingested_at=datetime.fromisoformat(row["ingested_at_iso"]),
        )

    # -- settlement (idempotent, append-only, hash-chained) ----------------
    def settle(self, bet_id: str, match_id: str, market: str, selection: str,
               entry_odds: float, closing_odds: Optional[float] = None
               ) -> Optional[SettlementRecord]:
        """Settle one bet against the ingested outcome.

        Returns the ledger record, or None if the outcome is not yet ingested
        (PENDING — caller retries when the result arrives). Idempotent by
        settlement_id; re-settling returns the existing immutable entry.
        """
        settlement_id = f"{match_id}:{market}:{selection}:{bet_id}"
        existing = self.conn.execute(
            "SELECT * FROM settlement_ledger WHERE settlement_id=?", (settlement_id,)
        ).fetchone()
        if existing:
            return self._row_to_record(existing)        # idempotent: no new entry

        outcome = self.get_outcome(match_id)
        if outcome is None:
            return None                                  # PENDING: no result yet

        result = resolve_market(market, selection, outcome)
        roi = _roi_for(result, entry_odds)
        clv = _clv_for(entry_odds, closing_odds)
        is_void = result == SettlementResult.VOID

        prev_hash = self._last_hash()
        payload = {
            "settlement_id": settlement_id, "match_id": match_id, "market": market,
            "selection": selection, "entry_odds": round(entry_odds, 6),
            "closing_odds": None if closing_odds is None else round(closing_odds, 6),
            "result": result.value, "realized_roi": round(roi, 6),
            "realized_clv": None if clv is None else round(clv, 6),
            "is_void": is_void,
        }
        entry_hash = _hash(prev_hash, payload)
        settled_at = _now_iso()
        self.conn.execute(
            "INSERT INTO settlement_ledger (settlement_id,match_id,market,selection,"
            "entry_odds,closing_odds,result,realized_roi,realized_clv,is_void,"
            "settled_at_iso,prev_hash,entry_hash) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (settlement_id, match_id, market, selection, entry_odds, closing_odds,
             result.value, roi, clv, 1 if is_void else 0, settled_at, prev_hash, entry_hash),
        )
        self.conn.commit()
        rec = SettlementRecord(settlement_id, match_id, market, selection, entry_odds,
                               closing_odds, result.value, roi, clv, is_void,
                               settled_at, prev_hash, entry_hash)
        rec.seq = self.conn.execute("SELECT MAX(seq) s FROM settlement_ledger").fetchone()["s"]
        return rec

    # -- replay / reconstruction -------------------------------------------
    def replay(self) -> SettlementSummary:
        """Deterministically fold the ledger into realized metrics + verify chain."""
        rows = self.conn.execute(
            "SELECT * FROM settlement_ledger ORDER BY seq"
        ).fetchall()
        n = len(rows)
        n_void = sum(1 for r in rows if r["is_void"])
        roi_vals = [r["realized_roi"] for r in rows if not r["is_void"]]
        clv_vals = [r["realized_clv"] for r in rows if r["realized_clv"] is not None]
        total_roi = sum(roi_vals)
        per_match: Dict[str, float] = {}
        for r in rows:
            if not r["is_void"]:
                per_match[r["match_id"]] = per_match.get(r["match_id"], 0.0) + r["realized_roi"]
        beat = (sum(1 for v in clv_vals if v > 0) / len(clv_vals)) if clv_vals else None
        return SettlementSummary(
            n_settlements=n, n_void=n_void, total_roi=round(total_roi, 6),
            mean_roi=round(total_roi / len(roi_vals), 6) if roi_vals else 0.0,
            mean_clv=round(sum(clv_vals) / len(clv_vals), 6) if clv_vals else None,
            beat_close_rate=round(beat, 6) if beat is not None else None,
            chain_valid=self.verify_chain(),
            per_match={k: round(v, 6) for k, v in per_match.items()},
        )

    def verify_chain(self) -> bool:
        """Recompute the hash chain; True iff untampered."""
        rows = self.conn.execute(
            "SELECT * FROM settlement_ledger ORDER BY seq"
        ).fetchall()
        prev = _GENESIS
        for r in rows:
            payload = {
                "settlement_id": r["settlement_id"], "match_id": r["match_id"],
                "market": r["market"], "selection": r["selection"],
                "entry_odds": round(r["entry_odds"], 6),
                "closing_odds": None if r["closing_odds"] is None else round(r["closing_odds"], 6),
                "result": r["result"], "realized_roi": round(r["realized_roi"], 6),
                "realized_clv": None if r["realized_clv"] is None else round(r["realized_clv"], 6),
                "is_void": bool(r["is_void"]),
            }
            if r["prev_hash"] != prev or r["entry_hash"] != _hash(prev, payload):
                return False
            prev = r["entry_hash"]
        return True

    # -- helpers -----------------------------------------------------------
    def _last_hash(self) -> str:
        row = self.conn.execute(
            "SELECT entry_hash FROM settlement_ledger ORDER BY seq DESC LIMIT 1"
        ).fetchone()
        return row["entry_hash"] if row else _GENESIS

    @staticmethod
    def _row_to_record(row: sqlite3.Row) -> SettlementRecord:
        rec = SettlementRecord(
            settlement_id=row["settlement_id"], match_id=row["match_id"],
            market=row["market"], selection=row["selection"], entry_odds=row["entry_odds"],
            closing_odds=row["closing_odds"], result=row["result"],
            realized_roi=row["realized_roi"], realized_clv=row["realized_clv"],
            is_void=bool(row["is_void"]), settled_at=row["settled_at_iso"],
            prev_hash=row["prev_hash"], entry_hash=row["entry_hash"],
        )
        rec.seq = row["seq"]
        return rec

    def close(self) -> None:
        self.conn.close()
