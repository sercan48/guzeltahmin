"""M4 market lifecycle state machine invariants. Pure-stdlib, no network."""

import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

from src.market.orchestration import (
    State, EventType, Outcome, Event, MatchLifecycle, LifecycleService, EventStore,
    is_legal_transition,
)

T0 = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)


def ev(match_id, etype, key, offset=0, payload=None):
    return Event(match_id, etype, key, T0 + timedelta(minutes=offset), payload or {})


def happy_path(match_id="m"):
    return [
        ev(match_id, EventType.MATCH_CREATED, "c", 0),
        ev(match_id, EventType.ODDS_UPDATED, "o1", 1),
        ev(match_id, EventType.ODDS_UPDATED, "o2", 2),
        ev(match_id, EventType.SIGNAL_GENERATED, "s1", 3),
        ev(match_id, EventType.MARKET_LOCKED, "l", 4),
        ev(match_id, EventType.MATCH_STARTED, "st", 5),
        ev(match_id, EventType.MATCH_FINISHED, "f", 6),
        ev(match_id, EventType.RESULT_CONFIRMED, "r", 7),
        ev(match_id, EventType.SETTLEMENT_COMPLETED, "set", 8),
    ]


class TestLifecycleTransitions(unittest.TestCase):
    def test_happy_path_reaches_settled(self):
        agg = MatchLifecycle.replay("m", happy_path())
        self.assertEqual(agg.state, State.SETTLED)
        self.assertTrue(agg.is_terminal())
        # 5 state-changing transitions: CREATED, ODDS->ACTIVE, LOCKED, STARTED, SETTLED
        self.assertEqual(agg.transition_count, 5)
        self.assertEqual(agg.invalid_transition_count, 0)

    def test_state_progression(self):
        agg = MatchLifecycle("m")
        self.assertEqual(agg.apply(ev("m", EventType.MATCH_CREATED, "c")).state, State.PREMATCH)
        self.assertEqual(agg.apply(ev("m", EventType.ODDS_UPDATED, "o1", 1)).state, State.ACTIVE)
        self.assertEqual(agg.apply(ev("m", EventType.MARKET_LOCKED, "l", 2)).state, State.LOCKED)
        self.assertEqual(agg.apply(ev("m", EventType.MATCH_STARTED, "st", 3)).state, State.CLOSED)

    def test_allowed_actions(self):
        agg = MatchLifecycle("m")
        agg.apply(ev("m", EventType.MATCH_CREATED, "c"))
        self.assertTrue(agg.can_generate_signal())
        agg.apply(ev("m", EventType.ODDS_UPDATED, "o", 1))
        self.assertTrue(agg.can_ingest_odds())
        agg.apply(ev("m", EventType.MARKET_LOCKED, "l", 2))
        self.assertFalse(agg.can_generate_signal())   # forbidden in LOCKED
        self.assertEqual(agg.allowed_actions(), {"capture_close"})

    # -- illegal transitions ------------------------------------------------
    def test_illegal_settlement_from_prematch(self):
        agg = MatchLifecycle("m")
        agg.apply(ev("m", EventType.MATCH_CREATED, "c"))
        r = agg.apply(ev("m", EventType.SETTLEMENT_COMPLETED, "x", 1))
        self.assertEqual(r.outcome, Outcome.INVALID)
        self.assertEqual(agg.state, State.PREMATCH)
        self.assertEqual(agg.invalid_transition_count, 1)

    def test_illegal_signal_in_locked(self):
        agg = MatchLifecycle.replay("m", happy_path()[:5])  # ...up to LOCKED
        self.assertEqual(agg.state, State.LOCKED)
        r = agg.apply(ev("m", EventType.SIGNAL_GENERATED, "sx", 10))
        self.assertEqual(r.outcome, Outcome.INVALID)

    def test_illegal_odds_in_closed(self):
        agg = MatchLifecycle.replay("m", happy_path()[:6])  # ...up to CLOSED
        self.assertEqual(agg.state, State.CLOSED)
        r = agg.apply(ev("m", EventType.ODDS_UPDATED, "ox", 10))
        self.assertEqual(r.outcome, Outcome.INVALID)

    def test_match_started_only_from_locked(self):
        agg = MatchLifecycle("m")
        agg.apply(ev("m", EventType.MATCH_CREATED, "c"))
        agg.apply(ev("m", EventType.ODDS_UPDATED, "o", 1))  # ACTIVE
        r = agg.apply(ev("m", EventType.MATCH_STARTED, "st", 2))
        self.assertEqual(r.outcome, Outcome.INVALID)

    def test_is_legal_transition_helper(self):
        self.assertTrue(is_legal_transition(None, EventType.MATCH_CREATED))
        self.assertTrue(is_legal_transition(State.PREMATCH, EventType.ODDS_UPDATED))
        self.assertFalse(is_legal_transition(State.LOCKED, EventType.SIGNAL_GENERATED))

    # -- guards -------------------------------------------------------------
    def test_settlement_requires_result_confirmed(self):
        agg = MatchLifecycle.replay("m", happy_path()[:6])  # CLOSED, not finished/confirmed
        r = agg.apply(ev("m", EventType.SETTLEMENT_COMPLETED, "set", 10))
        self.assertEqual(r.outcome, Outcome.INVALID)
        self.assertEqual(agg.state, State.CLOSED)

    def test_result_requires_finished(self):
        agg = MatchLifecycle.replay("m", happy_path()[:6])  # CLOSED, started, not finished
        r = agg.apply(ev("m", EventType.RESULT_CONFIRMED, "r", 10))
        self.assertEqual(r.outcome, Outcome.INVALID)

    # -- void / cancel ------------------------------------------------------
    def test_void_from_active(self):
        agg = MatchLifecycle.replay("m", happy_path()[:2])  # ACTIVE
        r = agg.apply(ev("m", EventType.MATCH_VOIDED, "v", 10))
        self.assertEqual(r.outcome, Outcome.APPLIED)
        self.assertEqual(agg.state, State.VOID)
        self.assertTrue(agg.is_terminal())

    def test_cancel_from_prematch(self):
        agg = MatchLifecycle("m")
        agg.apply(ev("m", EventType.MATCH_CREATED, "c"))
        r = agg.apply(ev("m", EventType.MATCH_CANCELLED, "x", 1))
        self.assertEqual(agg.state, State.CANCELLED)


class TestIdempotency(unittest.TestCase):
    def test_duplicate_key_is_noop(self):
        agg = MatchLifecycle("m")
        agg.apply(ev("m", EventType.MATCH_CREATED, "c"))
        agg.apply(ev("m", EventType.ODDS_UPDATED, "o", 1))
        r = agg.apply(ev("m", EventType.ODDS_UPDATED, "o", 1))  # same key
        self.assertEqual(r.outcome, Outcome.DUPLICATE)
        self.assertEqual(agg.duplicate_event_count, 1)
        self.assertEqual(agg.state, State.ACTIVE)

    def test_duplicate_settlement_safe(self):
        agg = MatchLifecycle.replay("m", happy_path())
        self.assertEqual(agg.state, State.SETTLED)
        # re-deliver settlement with a NEW key -> idempotent no-op, not double
        r = agg.apply(ev("m", EventType.SETTLEMENT_COMPLETED, "set2", 20))
        self.assertEqual(r.outcome, Outcome.IDEMPOTENT)
        self.assertEqual(agg.state, State.SETTLED)
        self.assertEqual(agg.transition_count, 5)

    def test_duplicate_signal_safe(self):
        agg = MatchLifecycle.replay("m", happy_path()[:4])  # ACTIVE w/ s1
        r = agg.apply(ev("m", EventType.SIGNAL_GENERATED, "s1", 3))  # same key
        self.assertEqual(r.outcome, Outcome.DUPLICATE)

    def test_idempotent_relock(self):
        agg = MatchLifecycle.replay("m", happy_path()[:5])  # LOCKED
        r = agg.apply(ev("m", EventType.MARKET_LOCKED, "l2", 10))  # new key, same milestone
        self.assertEqual(r.outcome, Outcome.IDEMPOTENT)
        self.assertEqual(agg.state, State.LOCKED)


class TestDeterminismAndRecovery(unittest.TestCase):
    def test_replay_determinism(self):
        a = MatchLifecycle.replay("m", happy_path())
        b = MatchLifecycle.replay("m", happy_path())
        self.assertEqual(a.snapshot(T0), b.snapshot(T0))

    def test_out_of_order_late_odds_rejected(self):
        # a delayed ODDS_UPDATED arriving after LOCKED must not mutate state
        evs = happy_path()[:5] + [ev("m", EventType.ODDS_UPDATED, "late", 10)]
        agg = MatchLifecycle.replay("m", evs)
        self.assertEqual(agg.state, State.LOCKED)
        self.assertEqual(agg.invalid_transition_count, 1)

    def test_service_matches_pure_replay(self):
        svc = LifecycleService()
        for e in happy_path():
            svc.handle(e)
        agg = svc.get("m")
        pure = MatchLifecycle.replay("m", happy_path())
        self.assertEqual(agg.snapshot(T0), pure.snapshot(T0))

    def test_crash_recovery_rebuilds_identical_state(self):
        path = os.path.join(tempfile.mkdtemp(), "events.db")
        try:
            svc1 = LifecycleService(EventStore(path))
            for e in happy_path():
                svc1.handle(e)
            snap1 = svc1.snapshot("m", T0)
            svc1.store.close()
            # fresh process / service on the same store -> rebuild from log
            svc2 = LifecycleService(EventStore(path))
            snap2 = svc2.snapshot("m", T0)
            self.assertEqual(snap1, snap2)
            self.assertEqual(snap2["current_state"], "SETTLED")
            svc2.store.close()
        finally:
            if os.path.exists(path):
                os.remove(path)

    def test_observability_counters(self):
        agg = MatchLifecycle("m")
        agg.apply(ev("m", EventType.MATCH_CREATED, "c"))
        agg.apply(ev("m", EventType.ODDS_UPDATED, "o", 1))
        agg.apply(ev("m", EventType.ODDS_UPDATED, "o", 1))   # duplicate
        agg.apply(ev("m", EventType.SETTLEMENT_COMPLETED, "bad", 2))  # illegal
        snap = agg.snapshot(T0)
        self.assertEqual(snap["current_state"], "ACTIVE")
        self.assertEqual(snap["duplicate_event_count"], 1)
        self.assertEqual(snap["invalid_transition_count"], 1)
        self.assertGreaterEqual(snap["transition_count"], 2)
        self.assertIsNotNone(snap["state_age"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
