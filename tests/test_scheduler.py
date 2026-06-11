"""M10.1 deterministic scheduler & snapshot engine invariants. No network."""

import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

from src.market.scheduler import (
    SnapshotScheduler, ManualClock, generate_schedule, SCHEDULE_TICKS,
)

KO = datetime(2026, 3, 1, 18, 0, tzinfo=timezone.utc)


class TestScheduleGeneration(unittest.TestCase):
    def test_seven_ticks_in_order(self):
        ev = generate_schedule("m", KO)
        self.assertEqual([e.tick for e in ev],
                         ["T-72h", "T-48h", "T-24h", "T-12h", "T-6h", "T-1h", "CLOSE"])

    def test_scheduled_times(self):
        ev = generate_schedule("m", KO)
        first = next(e for e in ev if e.tick == "T-72h")
        self.assertEqual(datetime.fromisoformat(first.scheduled_at), KO - timedelta(hours=72))
        close = next(e for e in ev if e.tick == "CLOSE")
        self.assertEqual(datetime.fromisoformat(close.scheduled_at), KO)

    def test_stable_ids_no_duplicates(self):
        ev = generate_schedule("m", KO)
        ids = [e.event_id for e in ev]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertIn("m:CLOSE", ids)

    def test_same_kickoff_identical_schedule(self):
        a = [e.__dict__ for e in generate_schedule("m", KO)]
        b = [e.__dict__ for e in generate_schedule("m", KO)]
        self.assertEqual(a, b)

    def test_trigger_type_mapping(self):
        ev = {e.tick: e.trigger_type() for e in generate_schedule("m", KO)}
        self.assertEqual(ev["CLOSE"], "MATCH_STARTED")
        self.assertEqual(ev["T-24h"], "ODDS_UPDATED")


class TestTriggerQueue(unittest.TestCase):
    def setUp(self):
        self.clock = ManualClock(KO - timedelta(hours=100))   # before all ticks
        self.s = SnapshotScheduler(self.clock, ":memory:")
        self.s.schedule_match("m", KO)

    def tearDown(self):
        self.s.close()

    def test_schedule_idempotent(self):
        self.s.schedule_match("m", KO)   # again
        self.assertEqual(self.s.replay()["n_scheduled"], 7)

    def test_due_releases_in_time_order(self):
        self.assertEqual(self.s.due(), [])           # nothing due yet
        self.clock.set(KO - timedelta(hours=24))     # T-72,48,24 are due
        due = [e.tick for e in self.s.due()]
        self.assertEqual(due, ["T-72h", "T-48h", "T-24h"])

    def test_due_idempotent_processing(self):
        self.clock.set(KO)                            # all due
        first = self.s.due()
        self.assertEqual(len(first), 7)
        self.assertEqual(self.s.due(), [])            # already dispatched -> none again

    def test_next_trigger(self):
        nxt = self.s.next_trigger()
        self.assertEqual(nxt.tick, "T-72h")
        self.clock.set(KO - timedelta(hours=47))      # T-72h, T-48h due (T-24h still future)
        self.s.due()                                  # dispatch T-72h, T-48h
        self.assertEqual(self.s.next_trigger().tick, "T-24h")


class TestCompleteness(unittest.TestCase):
    def setUp(self):
        self.clock = ManualClock(KO)
        self.s = SnapshotScheduler(self.clock, ":memory:")
        self.s.schedule_match("m", KO)

    def tearDown(self):
        self.s.close()

    def test_zero_then_monotonic_increase(self):
        self.assertEqual(self.s.completeness("m"), 0.0)
        prev = 0.0
        for tick in ("T-72h", "T-48h", "T-24h"):
            self.s.observe(f"m:{tick}")
            c = self.s.completeness("m")
            self.assertGreater(c, prev)
            prev = c

    def test_full_completeness(self):
        for _, tick in SCHEDULE_TICKS:
            self.s.observe(f"m:{tick}")
        self.assertEqual(self.s.completeness("m"), 1.0)

    def test_close_weighted_higher(self):
        # observing CLOSE (weight 2) raises completeness more than a normal tick
        s2 = SnapshotScheduler(ManualClock(KO), ":memory:")
        s2.schedule_match("z", KO)
        s2.observe("z:T-24h")
        c_normal = s2.completeness("z")
        s3 = SnapshotScheduler(ManualClock(KO), ":memory:")
        s3.schedule_match("z", KO)
        s3.observe("z:CLOSE")
        c_close = s3.completeness("z")
        self.assertGreater(c_close, c_normal)
        s2.close(); s3.close()

    def test_observe_unknown_event_false(self):
        self.assertFalse(self.s.observe("m:NOPE"))


class TestMonitoring(unittest.TestCase):
    def test_missed_snapshots(self):
        clock = ManualClock(KO + timedelta(hours=2))   # well past kickoff
        s = SnapshotScheduler(clock, ":memory:")
        s.schedule_match("m", KO)
        s.observe("m:CLOSE")                            # only CLOSE observed
        missed = s.missed_snapshots(grace_seconds=0)
        self.assertIn("m:T-24h", missed)
        self.assertNotIn("m:CLOSE", missed)
        s.close()

    def test_delayed_snapshots(self):
        clock = ManualClock(KO - timedelta(hours=72))
        s = SnapshotScheduler(clock, ":memory:")
        s.schedule_match("m", KO)
        clock.set(KO - timedelta(hours=70))             # 2h late for T-72h
        s.due()
        delayed = s.delayed_snapshots(tolerance_seconds=300)
        ids = [d[0] for d in delayed]
        self.assertIn("m:T-72h", ids)
        s.close()


class TestReplayDeterminism(unittest.TestCase):
    def _run(self, s, clock):
        s.schedule_match("m1", KO)
        s.schedule_match("m2", KO + timedelta(hours=1))
        clock.set(KO)
        for e in s.due():
            s.observe(e.event_id)

    def test_replay_deterministic(self):
        a = SnapshotScheduler(ManualClock(KO - timedelta(hours=100)), ":memory:")
        ca = a.clock
        self._run(a, ca)
        b = SnapshotScheduler(ManualClock(KO - timedelta(hours=100)), ":memory:")
        self._run(b, b.clock)
        self.assertEqual(a.replay(), b.replay())
        a.close(); b.close()

    def test_replay_rebuilds_identical_queue(self):
        path = os.path.join(tempfile.mkdtemp(), "sched.db")
        try:
            clock = ManualClock(KO - timedelta(hours=100))
            s = SnapshotScheduler(clock, path)
            self._run(s, clock)
            r1 = s.replay()
            s.close()
            s2 = SnapshotScheduler(ManualClock(KO), path)   # reopen
            self.assertEqual(s2.replay(), r1)
            s2.close()
        finally:
            if os.path.exists(path):
                os.remove(path)


if __name__ == "__main__":
    unittest.main(verbosity=2)
