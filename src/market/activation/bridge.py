"""M10.2 — Ingestion Bridge & Provider Activation Layer.

Connects M10.1 scheduler events to M2 Truth Store ingestion through the
provider abstraction, with a bounded, deterministic retry framework and an
append-only ingestion history. Outcome jobs route toward M8.1 (stub).

Additive: drives M10.1 SnapshotScheduler + M2 TruthStore (+ optional M8.1
SettlementLedger). NO network, no ML/prediction/betting. Replay-safe and
deterministic: identical (schedule, clock, provider fixture) reproduce identical
ingestion history and truth.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Dict, List, Optional, Sequence

from ..truth import TruthStore, RawSnapshot
from .providers import OddsProvider, ProviderError, ProviderQuote

# scheduler tick -> M2 snapshot_type (R1.1-aligned)
_TICK_TO_SNAPSHOT = {
    "T-72h": "OPEN", "T-48h": "OPEN", "T-24h": "T-24h", "T-12h": "T-12h",
    "T-6h": "T-6h", "T-1h": "T-1h", "CLOSE": "CLOSE",
}


class JobType(str, Enum):
    SNAPSHOT = "SNAPSHOT"
    CLOSE = "CLOSE"
    KICKOFF = "KICKOFF"
    OUTCOME = "OUTCOME"


class JobStatus(str, Enum):
    SUCCESS = "SUCCESS"
    RETRY = "RETRY"
    FAILED = "FAILED"


@dataclass
class IngestionJob:
    job_id: str
    match_id: str
    market: str
    tick: str
    job_type: str
    snapshot_type: str
    is_kickoff: bool = False


@dataclass
class JobResult:
    job_id: str
    status: str
    attempts: int
    providers_used: List[str]
    error: str = ""

    def to_dict(self) -> dict:
        return {"job_id": self.job_id, "status": self.status, "attempts": self.attempts,
                "providers_used": sorted(self.providers_used), "error": self.error}


class IngestionBridge:
    def __init__(self, scheduler, truth_store: TruthStore,
                 providers: Sequence[OddsProvider], settlement_ledger=None,
                 markets: Optional[List[str]] = None, max_retries: int = 2,
                 db_path: str = ":memory:") -> None:
        self.scheduler = scheduler
        self.truth = truth_store
        self.providers = list(providers)
        self.settlement = settlement_ledger
        self.markets = markets or ["1X2"]
        self.max_retries = max_retries
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS ingestion_jobs (
                job_id TEXT PRIMARY KEY, match_id TEXT, market TEXT, tick TEXT,
                job_type TEXT, snapshot_type TEXT, status TEXT, attempts INTEGER,
                providers_used TEXT, last_error TEXT, updated_iso TEXT
            );
            """
        )
        self.conn.commit()

    # -- main loop ----------------------------------------------------------
    def process_due(self) -> List[JobResult]:
        """Retry pending jobs, then process newly-due scheduler events."""
        results: List[JobResult] = []
        # 1. retry pending RETRY jobs (deterministic order)
        for row in self.conn.execute(
                "SELECT * FROM ingestion_jobs WHERE status=? ORDER BY job_id",
                (JobStatus.RETRY.value,)).fetchall():
            job = self._job_from_row(row)
            results.append(self._attempt(job, prior_attempts=row["attempts"]))
        # 2. newly-due scheduler events
        for ev in self.scheduler.due():
            for market in self.markets:
                job = self._job_for_event(ev, market)
                if self._already_succeeded(job.job_id):
                    continue                       # duplicate safety
                results.append(self._attempt(job, prior_attempts=0))
        return results

    # -- outcome integration stub (routes toward M8.1) ---------------------
    def ingest_outcome(self, match_id: str) -> JobResult:
        job_id = f"{match_id}:OUTCOME"
        if self._already_succeeded(job_id):
            return self._result_from_row(self._row(job_id))
        attempts = (self._row(job_id)["attempts"] if self._row(job_id) else 0)
        used: List[str] = []
        try:
            outcome = None
            for p in self.providers:
                o = p.fetch_outcome(match_id)
                if o is not None:
                    outcome, used = o, [p.name]
                    break
            if outcome is None:
                raise ProviderError("no outcome from any provider")
            if self.settlement is not None:
                from ..settlement import MatchOutcome
                self.settlement.ingest_outcome(MatchOutcome(
                    match_id, outcome.status, outcome.home_goals, outcome.away_goals,
                    source=used[0] if used else "provider"))
            return self._persist(IngestionJob(job_id, match_id, "-", "-",
                                              JobType.OUTCOME.value, "-"),
                                 JobStatus.SUCCESS, attempts + 1, used, "")
        except ProviderError as exc:
            attempts += 1
            status = JobStatus.RETRY if attempts <= self.max_retries else JobStatus.FAILED
            return self._persist(IngestionJob(job_id, match_id, "-", "-",
                                              JobType.OUTCOME.value, "-"),
                                 status, attempts, [], str(exc))

    # -- attempt one ingestion job -----------------------------------------
    def _attempt(self, job: IngestionJob, prior_attempts: int) -> JobResult:
        attempts = prior_attempts + 1
        collected_at = self._collected_at(job)
        used: List[str] = []
        try:
            quotes: List[ProviderQuote] = []
            for p in self.providers:
                try:
                    qs = p.fetch_snapshot(job.match_id, job.market, job.tick)
                except ProviderError:
                    continue                       # provider-level failover
                if qs:
                    quotes.extend(qs)
                    used.append(p.name)
            if not quotes:
                raise ProviderError("no quotes from any provider")
            for q in quotes:
                self.truth.ingest_snapshot(RawSnapshot(
                    job.match_id, q.provider, q.market, q.selection, q.odds,
                    job.snapshot_type, collected_at, provider_class=q.provider_class))
            self.truth.recompute_truth(job.match_id, job.market, job.snapshot_type)
            self.scheduler.observe(f"{job.match_id}:{job.tick}", at=collected_at)
            return self._persist(job, JobStatus.SUCCESS, attempts, used, "")
        except ProviderError as exc:
            status = JobStatus.RETRY if attempts <= self.max_retries else JobStatus.FAILED
            return self._persist(job, status, attempts, used, str(exc))

    # -- monitoring ---------------------------------------------------------
    def monitor(self) -> dict:
        rows = self.conn.execute("SELECT * FROM ingestion_jobs").fetchall()
        success = [r for r in rows if r["status"] == JobStatus.SUCCESS.value]
        provs: set = set()
        for r in success:
            provs.update(json.loads(r["providers_used"]))
        return {
            "ingestion_success": len(success),
            "ingestion_failure": sum(1 for r in rows if r["status"] == JobStatus.FAILED.value),
            "in_retry": sum(1 for r in rows if r["status"] == JobStatus.RETRY.value),
            "total_attempts": sum(r["attempts"] for r in rows),
            "provider_coverage": sorted(provs),
        }

    def replay(self) -> dict:
        rows = self.conn.execute(
            "SELECT job_id,status,attempts,providers_used FROM ingestion_jobs ORDER BY job_id"
        ).fetchall()
        return {"jobs": [{"job_id": r["job_id"], "status": r["status"],
                          "attempts": r["attempts"],
                          "providers_used": sorted(json.loads(r["providers_used"]))}
                         for r in rows]}

    # -- internals ----------------------------------------------------------
    def _job_for_event(self, ev, market: str) -> IngestionJob:
        snap = _TICK_TO_SNAPSHOT.get(ev.tick, "OPEN")
        jtype = JobType.CLOSE if ev.tick == "CLOSE" else JobType.SNAPSHOT
        return IngestionJob(f"{ev.event_id}:{market}", ev.match_id, market, ev.tick,
                            jtype.value, snap, is_kickoff=(ev.tick == "CLOSE"))

    def _collected_at(self, job: IngestionJob) -> datetime:
        row = self.scheduler.conn.execute(
            "SELECT scheduled_at_iso FROM schedule WHERE event_id=?",
            (f"{job.match_id}:{job.tick}",)).fetchone()
        return datetime.fromisoformat(row["scheduled_at_iso"]) if row else self.scheduler.clock.now()

    def _already_succeeded(self, job_id: str) -> bool:
        r = self._row(job_id)
        return r is not None and r["status"] == JobStatus.SUCCESS.value

    def _row(self, job_id: str):
        return self.conn.execute("SELECT * FROM ingestion_jobs WHERE job_id=?",
                                 (job_id,)).fetchone()

    def _persist(self, job: IngestionJob, status: JobStatus, attempts: int,
                 used: List[str], error: str) -> JobResult:
        self.conn.execute(
            "INSERT INTO ingestion_jobs (job_id,match_id,market,tick,job_type,snapshot_type,"
            "status,attempts,providers_used,last_error,updated_iso) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(job_id) DO UPDATE SET status=excluded.status,"
            "attempts=excluded.attempts,providers_used=excluded.providers_used,"
            "last_error=excluded.last_error,updated_iso=excluded.updated_iso",
            (job.job_id, job.match_id, job.market, job.tick, job.job_type, job.snapshot_type,
             status.value, attempts, json.dumps(sorted(used)), error,
             self.scheduler.clock.now().isoformat()),
        )
        self.conn.commit()
        return JobResult(job.job_id, status.value, attempts, used, error)

    def _job_from_row(self, row) -> IngestionJob:
        return IngestionJob(row["job_id"], row["match_id"], row["market"], row["tick"],
                            row["job_type"], row["snapshot_type"],
                            is_kickoff=(row["tick"] == "CLOSE"))

    def _result_from_row(self, row) -> JobResult:
        return JobResult(row["job_id"], row["status"], row["attempts"],
                         json.loads(row["providers_used"]), row["last_error"] or "")

    def close(self) -> None:
        self.conn.close()
