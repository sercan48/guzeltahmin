"""M2 — Truth Store (SQLite-backed, OBSERVED-only).

Realizes the SYSTEM TRUTH RULE choke point: downstream consumers read canonical
truth from here, never from raw providers. Pure-stdlib (sqlite3), network-free.

Scope of this module (M2):
- append-only raw snapshot ledger (audit)
- canonical `truth_odds` computed via M1 de-vig (per book) + cross-book
  trust-weighted consensus, tagged provenance=OBSERVED with a confidence score
- point-in-time read API (as_of)

Out of scope here (later modules):
- RECONSTRUCTED closing-line backfill (M2.2)
- dynamic CA-based trust / drift (uses static class weights for now)
- orchestration / incremental triggers (M4)

References: MIW_SHARP_DATA_INFRASTRUCTURE (F16), MIW_TRUTH_WAREHOUSE_BOOTSTRAP
(§3 schema, §4 closing-line, §7 DQ).
"""

from __future__ import annotations

import json
import math
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Dict, List, Mapping, Optional, Sequence

from .canonicalization import devig, DevigMethod


class ProviderClass(str, Enum):
    SHARP = "SHARP"            # Pinnacle, Betfair
    SEMI_SHARP = "SEMI_SHARP"  # major books
    SOFT = "SOFT"              # recreational books
    FREE = "FREE"              # free / scraped APIs


class Provenance(str, Enum):
    OBSERVED = "OBSERVED"
    RECONSTRUCTED = "RECONSTRUCTED"   # produced by M2.2, not this module


# Static base trust by class (dynamic CA-based weighting arrives with real
# closing data; see F16 §2.2). Tunable via TruthStore(trust_weights=...).
DEFAULT_CLASS_TRUST: Dict[str, float] = {
    ProviderClass.SHARP.value: 1.00,
    ProviderClass.SEMI_SHARP.value: 0.55,
    ProviderClass.SOFT.value: 0.30,
    ProviderClass.FREE.value: 0.12,
}

# Minimal known-provider classification (overridable).
DEFAULT_PROVIDER_CLASS: Dict[str, str] = {
    "pinnacle": ProviderClass.SHARP.value,
    "betfair": ProviderClass.SHARP.value,
    "bet365": ProviderClass.SEMI_SHARP.value,
    "williamhill": ProviderClass.SEMI_SHARP.value,
    "the_odds_api": ProviderClass.SOFT.value,
    "obscurebet": ProviderClass.SOFT.value,
}


def classify_provider(provider: str,
                      overrides: Optional[Mapping[str, str]] = None) -> str:
    table = dict(DEFAULT_PROVIDER_CLASS)
    if overrides:
        table.update(overrides)
    return table.get(provider.lower(), ProviderClass.FREE.value)


def _epoch(ts: datetime) -> float:
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(timezone.utc).timestamp()


@dataclass
class RawSnapshot:
    match_id: str
    provider: str
    market: str
    selection: str
    odds: float
    snapshot_type: str
    collected_at: datetime
    provider_class: Optional[str] = None     # inferred if None
    source_id: str = "unknown"


@dataclass
class TruthRecord:
    match_id: str
    market: str
    selection: str
    snapshot_type: str
    as_of: str                       # ISO UTC
    p_truth: float
    o_truth: float
    sigma_truth: float               # cross-provider disagreement
    provenance: str
    confidence: float                # 0..1
    contributing_providers: Dict[str, float] = field(default_factory=dict)
    devig_method: str = DevigMethod.ENSEMBLE.value

    def to_dict(self) -> dict:
        d = self.__dict__.copy()
        return d


class TruthStore:
    """SQLite-backed canonical truth store (OBSERVED-only in M2)."""

    def __init__(self, db_path: str = ":memory:",
                 trust_weights: Optional[Mapping[str, float]] = None,
                 provider_class_overrides: Optional[Mapping[str, str]] = None) -> None:
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
        self.trust = dict(DEFAULT_CLASS_TRUST)
        if trust_weights:
            self.trust.update(trust_weights)
        self.class_overrides = dict(provider_class_overrides or {})
        self._init_schema()

    # -- schema -------------------------------------------------------------
    def _init_schema(self) -> None:
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS odds_snapshot_raw (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                match_id TEXT, provider TEXT, provider_class TEXT,
                market TEXT, selection TEXT, odds REAL,
                snapshot_type TEXT, collected_at_ts REAL, collected_at_iso TEXT,
                source_id TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_raw_group
                ON odds_snapshot_raw(match_id, market, snapshot_type);

            CREATE TABLE IF NOT EXISTS truth_odds (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                match_id TEXT, market TEXT, selection TEXT, snapshot_type TEXT,
                as_of_ts REAL, as_of_iso TEXT,
                p_truth REAL, o_truth REAL, sigma_truth REAL,
                provenance TEXT, confidence REAL,
                contributing TEXT, devig_method TEXT, version INTEGER
            );
            CREATE INDEX IF NOT EXISTS idx_truth_read
                ON truth_odds(match_id, market, selection, snapshot_type, as_of_ts);
            """
        )
        self.conn.commit()

    # -- ingest -------------------------------------------------------------
    def ingest_snapshot(self, snap: RawSnapshot) -> None:
        pclass = snap.provider_class or classify_provider(snap.provider, self.class_overrides)
        self.conn.execute(
            "INSERT INTO odds_snapshot_raw (match_id,provider,provider_class,market,"
            "selection,odds,snapshot_type,collected_at_ts,collected_at_iso,source_id) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (snap.match_id, snap.provider, pclass, snap.market, snap.selection,
             float(snap.odds), snap.snapshot_type, _epoch(snap.collected_at),
             snap.collected_at.astimezone(timezone.utc).isoformat(), snap.source_id),
        )
        self.conn.commit()

    def ingest_many(self, snaps: Sequence[RawSnapshot]) -> None:
        for s in snaps:
            self.ingest_snapshot(s)

    # -- compute canonical truth -------------------------------------------
    def recompute_truth(self, match_id: str, market: str,
                        snapshot_type: str) -> List[TruthRecord]:
        """Compute OBSERVED truth for one (match, market, snapshot_type).

        Steps: group raw by provider -> de-vig each book (M1 ensemble) ->
        trust-weighted consensus per selection -> renormalize -> persist.
        """
        rows = self.conn.execute(
            "SELECT provider, provider_class, selection, odds, collected_at_ts, "
            "collected_at_iso FROM odds_snapshot_raw "
            "WHERE match_id=? AND market=? AND snapshot_type=?",
            (match_id, market, snapshot_type),
        ).fetchall()
        if not rows:
            return []

        # latest quote per (provider, selection)
        per_book: Dict[str, Dict[str, float]] = {}
        per_book_class: Dict[str, str] = {}
        latest_ts: Dict[tuple, float] = {}
        as_of_ts = 0.0
        as_of_iso = ""
        for r in rows:
            key = (r["provider"], r["selection"])
            if r["collected_at_ts"] >= latest_ts.get(key, -1):
                latest_ts[key] = r["collected_at_ts"]
                per_book.setdefault(r["provider"], {})[r["selection"]] = r["odds"]
                per_book_class[r["provider"]] = r["provider_class"]
            if r["collected_at_ts"] >= as_of_ts:
                as_of_ts = r["collected_at_ts"]
                as_of_iso = r["collected_at_iso"]

        # de-vig each book that quotes the full market
        selections = sorted({r["selection"] for r in rows})
        fair_by_book: Dict[str, Dict[str, float]] = {}
        weights: Dict[str, float] = {}
        for provider, odds in per_book.items():
            if len(odds) < len(selections):
                continue  # need the full market to de-vig
            res = devig(odds, DevigMethod.ENSEMBLE)
            fair_by_book[provider] = res.fair_probs
            weights[provider] = self.trust.get(per_book_class[provider], 0.1)

        if not fair_by_book:
            return []

        wsum = sum(weights.values()) or 1.0
        records: List[TruthRecord] = []
        consensus: Dict[str, float] = {}
        sigma: Dict[str, float] = {}
        for s in selections:
            vals = [(fair_by_book[b][s], weights[b]) for b in fair_by_book if s in fair_by_book[b]]
            if not vals:
                continue
            mean = sum(p * w for p, w in vals) / sum(w for _, w in vals)
            consensus[s] = mean
            sigma[s] = self._wstd([p for p, _ in vals], [w for _, w in vals], mean)
        norm = sum(consensus.values()) or 1.0
        consensus = {s: p / norm for s, p in consensus.items()}

        confidence = self._confidence(fair_by_book, per_book_class, sigma)
        contributing = {b: round(weights[b] / wsum, 4) for b in fair_by_book}

        for s in selections:
            if s not in consensus:
                continue
            p = consensus[s]
            rec = TruthRecord(
                match_id=match_id, market=market, selection=s,
                snapshot_type=snapshot_type, as_of=as_of_iso,
                p_truth=p, o_truth=(1.0 / p if p > 0 else float("inf")),
                sigma_truth=sigma.get(s, 0.0), provenance=Provenance.OBSERVED.value,
                confidence=confidence, contributing_providers=contributing,
            )
            self._persist(rec, as_of_ts)
            records.append(rec)
        return records

    def recompute_all(self) -> int:
        groups = self.conn.execute(
            "SELECT DISTINCT match_id, market, snapshot_type FROM odds_snapshot_raw"
        ).fetchall()
        n = 0
        for g in groups:
            n += len(self.recompute_truth(g["match_id"], g["market"], g["snapshot_type"]))
        return n

    # -- read API (point-in-time) ------------------------------------------
    def get_truth(self, match_id: str, market: str, selection: str,
                  snapshot_type: Optional[str] = None,
                  as_of: Optional[datetime] = None) -> Optional[TruthRecord]:
        sql = ("SELECT * FROM truth_odds WHERE match_id=? AND market=? AND selection=?")
        params: list = [match_id, market, selection]
        if snapshot_type is not None:
            sql += " AND snapshot_type=?"
            params.append(snapshot_type)
        if as_of is not None:
            sql += " AND as_of_ts<=?"
            params.append(_epoch(as_of))
        sql += " ORDER BY as_of_ts DESC, version DESC LIMIT 1"
        row = self.conn.execute(sql, params).fetchone()
        return self._row_to_record(row) if row else None

    def get_truth_market(self, match_id: str, market: str,
                         snapshot_type: Optional[str] = None,
                         as_of: Optional[datetime] = None) -> Dict[str, TruthRecord]:
        """Latest truth per selection for a market (the P_truth map downstream uses)."""
        sels = self.conn.execute(
            "SELECT DISTINCT selection FROM truth_odds WHERE match_id=? AND market=?",
            (match_id, market),
        ).fetchall()
        out: Dict[str, TruthRecord] = {}
        for s in sels:
            rec = self.get_truth(match_id, market, s["selection"], snapshot_type, as_of)
            if rec:
                out[s["selection"]] = rec
        return out

    def get_closing_truth(self, match_id: str, market: str,
                          selection: str) -> Optional[TruthRecord]:
        return self.get_truth(match_id, market, selection, snapshot_type="CLOSE")

    # -- helpers ------------------------------------------------------------
    def _persist(self, rec: TruthRecord, as_of_ts: float) -> None:
        prev = self.conn.execute(
            "SELECT MAX(version) v FROM truth_odds WHERE match_id=? AND market=? "
            "AND selection=? AND snapshot_type=?",
            (rec.match_id, rec.market, rec.selection, rec.snapshot_type),
        ).fetchone()
        version = (prev["v"] or 0) + 1
        self.conn.execute(
            "INSERT INTO truth_odds (match_id,market,selection,snapshot_type,as_of_ts,"
            "as_of_iso,p_truth,o_truth,sigma_truth,provenance,confidence,contributing,"
            "devig_method,version) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (rec.match_id, rec.market, rec.selection, rec.snapshot_type, as_of_ts,
             rec.as_of, rec.p_truth, rec.o_truth, rec.sigma_truth, rec.provenance,
             rec.confidence, json.dumps(rec.contributing_providers),
             rec.devig_method, version),
        )
        self.conn.commit()

    @staticmethod
    def _row_to_record(row: sqlite3.Row) -> TruthRecord:
        return TruthRecord(
            match_id=row["match_id"], market=row["market"], selection=row["selection"],
            snapshot_type=row["snapshot_type"], as_of=row["as_of_iso"],
            p_truth=row["p_truth"], o_truth=row["o_truth"], sigma_truth=row["sigma_truth"],
            provenance=row["provenance"], confidence=row["confidence"],
            contributing_providers=json.loads(row["contributing"]),
            devig_method=row["devig_method"],
        )

    @staticmethod
    def _wstd(vals: List[float], weights: List[float], mean: float) -> float:
        wsum = sum(weights)
        if wsum <= 0 or len(vals) < 2:
            return 0.0
        var = sum(w * (v - mean) ** 2 for v, w in zip(vals, weights)) / wsum
        return math.sqrt(max(0.0, var))

    def _confidence(self, fair_by_book, per_book_class, sigma) -> float:
        has_sharp = any(c == ProviderClass.SHARP.value for c in per_book_class.values())
        base = 1.0 if has_sharp else 0.6
        mean_sigma = (sum(sigma.values()) / len(sigma)) if sigma else 0.0
        agreement = max(0.0, 1.0 - mean_sigma / 0.05)   # 5pp disagreement -> 0
        n_factor = min(1.0, len(fair_by_book) / 3.0)
        return round(max(0.0, min(1.0, base * (0.5 + 0.5 * agreement) * (0.6 + 0.4 * n_factor))), 4)

    def close(self) -> None:
        self.conn.close()
