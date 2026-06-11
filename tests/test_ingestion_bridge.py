"""M10.2 ingestion bridge & provider activation invariants. No network."""

import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

from src.market.truth import TruthStore
from src.market.scheduler import SnapshotScheduler, ManualClock
from src.market.settlement import SettlementLedger
from src.market.activation import (
    IngestionBridge, MockOddsProvider, ProviderOutcome, JobStatus, JobType,
)

KO = datetime(2026, 3, 1, 18, 0, tzinfo=timezone.utc)


def odds_fixture(match="m"):
    base = {"HOME": 2.0, "DRAW": 3.4, "AWAY": 3.6}
    return {match: {tick: dict(base) for tick in
                    ("T-72h", "T-48h", "T-24h", "T-12h", "T-6h", "T-1h", "CLOSE")}}


def make(clock_t=KO, fail=None, providers=None, settlement=None):
    clock = ManualClock(clock_t)
    sched = SnapshotScheduler(clock, ":memory:")
    truth = TruthStore(":memory:")
    if providers is None:
        providers = [
            MockOddsProvider("pinnacle", "SHARP", odds_fixture(),
                             outcomes={"m": ProviderOutcome("COMPLETED", 2, 1)},
                             fail_matches=fail),
            MockOddsProvider("bet365", "SEMI_SHARP", odds_fixture()),
        ]
    bridge = IngestionBridge(sched, truth, providers, settlement_ledger=settlement,
                             db_path=":memory:")
    return clock, sched, truth, bridge


class TestSchedulerToIngestion(unittest.TestCase):
    def test_due_events_create_jobs_and_ingest_truth(self):
        clock, sched, truth, bridge = make()
        sched.schedule_match("m", KO)
        clock.set(KO)                       # all 7 ticks due
        results = bridge.process_due()
        self.assertEqual(len(results), 7)
        self.assertTrue(all(r.status == JobStatus.SUCCESS.value for r in results))
        # Truth Store received the CLOSE snapshot
        close = truth.get_closing_truth("m", "1X2", "HOME")
        self.assertIsNotNone(close)
        self.assertGreater(close.o_truth, 1.0)
        bridge.close()

    def test_job_types(self):
        clock, sched, truth, bridge = make()
        sched.schedule_match("m", KO)
        clock.set(KO)
        bridge.process_due()
        rows = {r["tick"]: r["job_type"] for r in
                bridge.conn.execute("SELECT tick, job_type FROM ingestion_jobs").fetchall()}
        self.assertEqual(rows["CLOSE"], JobType.CLOSE.value)
        self.assertEqual(rows["T-24h"], JobType.SNAPSHOT.value)
        bridge.close()

    def test_provider_class_provenance_preserved(self):
        clock, sched, truth, bridge = make()
        sched.schedule_match("m", KO)
        clock.set(KO)
        bridge.process_due()
        home = truth.get_closing_truth("m", "1X2", "HOME")
        # both providers contributed; sharp anchor present, OBSERVED provenance
        self.assertIn("pinnacle", home.contributing_providers)
        self.assertEqual(home.provenance, "OBSERVED")
        self.assertGreater(home.confidence, 0.0)
        bridge.close()


class TestDuplicateSafety(unittest.TestCase):
    def test_duplicate_due_no_duplicate_truth(self):
        clock, sched, truth, bridge = make()
        sched.schedule_match("m", KO)
        clock.set(KO)
        bridge.process_due()
        raw1 = truth.conn.execute("SELECT COUNT(*) c FROM odds_snapshot_raw").fetchone()["c"]
        bridge.process_due()                # nothing new due; jobs already SUCCESS
        raw2 = truth.conn.execute("SELECT COUNT(*) c FROM odds_snapshot_raw").fetchone()["c"]
        self.assertEqual(raw1, raw2)        # no duplicate raw rows
        bridge.close()


class TestRetryFramework(unittest.TestCase):
    def test_failing_provider_retries_then_failed(self):
        # both providers fail for match -> no quotes -> retry to exhaustion
        providers = [MockOddsProvider("p1", "SHARP", odds_fixture(), fail_matches={"m"}),
                     MockOddsProvider("p2", "SOFT", odds_fixture(), fail_matches={"m"})]
        clock, sched, truth, bridge = make(providers=providers)
        sched.schedule_match("m", KO)
        clock.set(KO - timedelta(hours=72))   # only T-72h due
        r = bridge.process_due()[0]
        self.assertEqual(r.status, JobStatus.RETRY.value)
        self.assertEqual(r.attempts, 1)
        bridge.process_due(); bridge.process_due()   # exhaust max_retries=2
        row = bridge.conn.execute("SELECT status, attempts FROM ingestion_jobs").fetchone()
        self.assertEqual(row["status"], JobStatus.FAILED.value)
        self.assertGreaterEqual(row["attempts"], 3)
        bridge.close()

    def test_provider_failover_partial(self):
        # one provider fails, the other succeeds -> job SUCCESS with one provider
        providers = [MockOddsProvider("good", "SHARP", odds_fixture()),
                     MockOddsProvider("bad", "SOFT", odds_fixture(), fail_matches={"m"})]
        clock, sched, truth, bridge = make(providers=providers)
        sched.schedule_match("m", KO); clock.set(KO)
        results = bridge.process_due()
        self.assertTrue(all(r.status == JobStatus.SUCCESS.value for r in results))
        self.assertEqual(bridge.monitor()["provider_coverage"], ["good"])
        bridge.close()


class TestOutcomeStub(unittest.TestCase):
    def test_outcome_routes_to_m81(self):
        settle = SettlementLedger(":memory:")
        clock, sched, truth, bridge = make(settlement=settle)
        r = bridge.ingest_outcome("m")
        self.assertEqual(r.status, JobStatus.SUCCESS.value)
        got = settle.get_outcome("m")
        self.assertEqual((got.home_goals, got.away_goals), (2, 1))
        bridge.close(); settle.close()

    def test_outcome_idempotent(self):
        settle = SettlementLedger(":memory:")
        clock, sched, truth, bridge = make(settlement=settle)
        bridge.ingest_outcome("m"); bridge.ingest_outcome("m")
        n = settle.conn.execute("SELECT COUNT(*) c FROM match_outcomes").fetchone()["c"]
        self.assertEqual(n, 1)
        bridge.close(); settle.close()


class TestMonitoringAndReplay(unittest.TestCase):
    def _run(self, bridge, sched, clock):
        sched.schedule_match("m", KO)
        clock.set(KO)
        bridge.process_due()

    def test_monitor_fields(self):
        clock, sched, truth, bridge = make()
        self._run(bridge, sched, clock)
        mon = bridge.monitor()
        for k in ("ingestion_success", "ingestion_failure", "in_retry",
                  "total_attempts", "provider_coverage"):
            self.assertIn(k, mon)
        self.assertEqual(mon["ingestion_success"], 7)
        self.assertEqual(sorted(mon["provider_coverage"]), ["bet365", "pinnacle"])
        bridge.close()

    def test_replay_deterministic(self):
        c1, s1, t1, b1 = make()
        self._run(b1, s1, c1)
        c2, s2, t2, b2 = make()
        self._run(b2, s2, c2)
        self.assertEqual(b1.replay(), b2.replay())
        # truth identical too
        self.assertEqual(t1.get_closing_truth("m", "1X2", "HOME").o_truth,
                         t2.get_closing_truth("m", "1X2", "HOME").o_truth)
        b1.close(); b2.close()

    def test_replay_safe_reopen(self):
        path = os.path.join(tempfile.mkdtemp(), "ingest.db")
        try:
            clock = ManualClock(KO)
            sched = SnapshotScheduler(clock, ":memory:")
            truth = TruthStore(":memory:")
            providers = [MockOddsProvider("pinnacle", "SHARP", odds_fixture())]
            b = IngestionBridge(sched, truth, providers, db_path=path)
            sched.schedule_match("m", KO); clock.set(KO); b.process_due()
            r1 = b.replay()
            b.close()
            b2 = IngestionBridge(sched, truth, providers, db_path=path)
            self.assertEqual(b2.replay(), r1)
            b2.close()
        finally:
            if os.path.exists(path):
                os.remove(path)


if __name__ == "__main__":
    unittest.main(verbosity=2)
