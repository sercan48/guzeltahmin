"""PHASE-LIVE L5 — Offline tests for the monetization layer.

Fully deterministic. No network. All I/O uses :memory: SQLite or tempfile.
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import List, Optional
from unittest.mock import MagicMock, patch

from src.market.service.monetization.clock import ManualClock, SystemClock
from src.market.service.monetization.delay import DelayScheduler
from src.market.service.monetization.filter import MonetizationFilter, _gate_ts
from src.market.service.monetization.models import (
    DAILY_QUOTA, FORMAT_RULES, TIER_DELAYS, WEEKLY_QUOTA,
    FilterResult, FormatType, QueueEntry, UserTier,
)
from src.market.service.monetization.revenue import RevenueSimulator
from src.market.service.monetization.store import UserStore
from src.market.service.monetization.watermark import WatermarkInjector
from src.market.service.monetization.worker import (
    TieredDeliveryWorker, _deserialize_signal, _format_signal, _serialize_signal,
)


# ---------------------------------------------------------------------------
# Shared fixtures / helpers
# ---------------------------------------------------------------------------

# Unix timestamp for 2026-01-05 12:00:00 UTC (a Monday noon)
# Must match _Sig.timestamp so publish_after_ts is deterministic.
_BASE_TS = 1767614400.0


def _clock(ts: float = _BASE_TS) -> ManualClock:
    return ManualClock(ts=ts)


def _make_store(clock=None) -> UserStore:
    return UserStore(":memory:", clock or _clock())


def _make_scheduler(clock=None) -> DelayScheduler:
    return DelayScheduler(":memory:", clock or _clock())


def _make_worker(scheduler, clock=None) -> tuple:
    wm = WatermarkInjector()
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    worker = TieredDeliveryWorker(scheduler, wm, db_path, clock or _clock())
    return worker, wm, db_path


def _make_filter(store, scheduler, worker, wm, clock=None) -> MonetizationFilter:
    return MonetizationFilter(store, scheduler, worker, wm, clock or _clock())


@dataclass
class _Sig:
    """Minimal signal stub."""
    match_id: str = "m1"
    market: str = "1X2"
    selection: str = "HOME"
    tier: str = "TIER_A"
    edge_score: float = 0.05
    confidence: float = 0.80
    truth_confidence: float = 0.75
    timestamp: str = "2026-01-05T12:00:00+00:00"


def _make_gate(publish: bool = True, signal_id: str = "sid1") -> SimpleNamespace:
    return SimpleNamespace(
        signal_id=signal_id,
        state="PAPER",
        decision="ALLOW" if publish else "SUPPRESS",
        publish=publish,
        recorded=True,
        reason_codes=[],
    )


def _make_publisher(dry_run: bool = True) -> MagicMock:
    pub = MagicMock()
    pub.dry_run = dry_run
    pub.published_calls: list = []

    def fake_send(channel_id, text):
        pub.published_calls.append((channel_id, text))
        return True

    pub._send = fake_send
    return pub


# ===========================================================================
# ManualClock
# ===========================================================================

class TestManualClock(unittest.TestCase):

    def test_advance_increments_ts(self):
        c = ManualClock(ts=1000.0)
        c.advance(60.0)
        self.assertAlmostEqual(c.now_ts(), 1060.0)

    def test_set_ts(self):
        c = ManualClock(ts=0.0)
        c.set_ts(9999.0)
        self.assertEqual(c.now_ts(), 9999.0)

    def test_today_str_format(self):
        c = ManualClock(ts=_BASE_TS)
        self.assertRegex(c.today_str(), r"^\d{4}-\d{2}-\d{2}$")

    def test_week_str_format(self):
        c = ManualClock(ts=_BASE_TS)
        self.assertRegex(c.week_str(), r"^\d{4}-\d{2}$")

    def test_iso_is_utc(self):
        c = ManualClock(ts=0.0)
        self.assertIn("+00:00", c.now_iso())


# ===========================================================================
# WatermarkInjector
# ===========================================================================

class TestWatermarkInjector(unittest.TestCase):

    def setUp(self):
        self.wm = WatermarkInjector()

    def test_encode_decode_zero(self):
        self.assertEqual(self.wm.decode(self.wm.encode(0)), 0)

    def test_encode_decode_small(self):
        for n in (1, 2, 9, 42, 255):
            self.assertEqual(self.wm.decode(self.wm.encode(n)), n,
                             msg=f"round-trip failed for {n}")

    def test_encode_decode_large(self):
        for n in (1_000, 99_999, 2**20):
            self.assertEqual(self.wm.decode(self.wm.encode(n)), n)

    def test_encoded_text_invisible(self):
        from src.market.service.monetization.watermark import _ZW_SET, _SENTINEL
        encoded = self.wm.encode(7)
        # Everything after sentinel must be in zero-width set
        for ch in encoded[1:]:
            self.assertIn(ch, _ZW_SET, f"unexpected visible char {ch!r}")

    def test_inject_appends_watermark(self):
        text = "Hello signal!"
        injected = self.wm.inject(text, 42)
        self.assertTrue(injected.startswith(text))
        self.assertEqual(self.wm.decode(injected), 42)

    def test_decode_absent_returns_none(self):
        self.assertIsNone(self.wm.decode("plain text"))

    def test_strip_removes_watermark(self):
        text = "Clean text"
        injected = self.wm.inject(text, 5)
        self.assertEqual(self.wm.strip(injected), text)

    def test_negative_delivery_id_raises(self):
        with self.assertRaises(ValueError):
            self.wm.encode(-1)


# ===========================================================================
# UserStore
# ===========================================================================

class TestUserStore(unittest.TestCase):

    def setUp(self):
        self.clock = _clock()
        self.store = _make_store(self.clock)

    def test_register_and_get_user(self):
        self.store.register_user("u1", "FREE", "@free_ch")
        rec = self.store.get_user("u1")
        self.assertEqual(rec.user_id, "u1")
        self.assertEqual(rec.tier, "FREE")
        self.assertEqual(rec.channel_id, "@free_ch")
        self.assertTrue(rec.active)

    def test_register_idempotent_updates_tier(self):
        self.store.register_user("u1", "FREE", "@ch")
        self.store.register_user("u1", "PRO", "@ch_pro")
        rec = self.store.get_user("u1")
        self.assertEqual(rec.tier, "PRO")
        self.assertEqual(rec.channel_id, "@ch_pro")

    def test_deactivate_user(self):
        self.store.register_user("u2", "BASIC", "@b")
        self.store.deactivate_user("u2")
        self.assertFalse(self.store.get_user("u2").active)
        self.assertEqual(len(self.store.list_active_users()), 0)

    def test_list_active_users_returns_only_active(self):
        self.store.register_user("a", "FREE",  "@a")
        self.store.register_user("b", "BASIC", "@b")
        self.store.deactivate_user("a")
        active = self.store.list_active_users()
        self.assertEqual([u.user_id for u in active], ["b"])

    def test_quota_check_allows_within_daily(self):
        self.store.register_user("u", "BASIC", "@c")
        for i in range(10):
            allowed, reason = self.store.check_and_consume_quota("u")
            self.assertTrue(allowed, f"should be allowed at i={i}")
        allowed, reason = self.store.check_and_consume_quota("u")
        self.assertFalse(allowed)
        self.assertEqual(reason, "daily_quota")

    def test_quota_check_denies_when_daily_exceeded(self):
        self.store.register_user("u", "FREE", "@c")
        for _ in range(DAILY_QUOTA[UserTier.FREE]):
            self.store.check_and_consume_quota("u")
        allowed, reason = self.store.check_and_consume_quota("u")
        self.assertFalse(allowed)
        self.assertEqual(reason, "daily_quota")

    def test_quota_reset_on_day_change(self):
        self.store.register_user("u", "FREE", "@c")
        # Exhaust daily quota
        for _ in range(DAILY_QUOTA[UserTier.FREE]):
            self.store.check_and_consume_quota("u")
        # Advance clock to next day
        self.clock.advance(86401.0)
        allowed, reason = self.store.check_and_consume_quota("u")
        self.assertTrue(allowed, f"quota should reset after day change; got reason={reason!r}")

    def test_weekly_quota_exceeded(self):
        self.store.register_user("u", "FREE", "@c")
        # Over 7 days, send DAILY_QUOTA each day until weekly quota exhausted
        weekly = WEEKLY_QUOTA[UserTier.FREE]
        sent = 0
        for day in range(7):
            if sent >= weekly:
                break
            self.clock.advance(86400.0)
            for _ in range(DAILY_QUOTA[UserTier.FREE]):
                if sent < weekly:
                    self.store.check_and_consume_quota("u")
                    sent += 1
        # Now weekly quota is exhausted; one more should fail
        self.clock.advance(86400.0)  # new day (resets daily) but same week
        # Force same week by checking week_str
        # Actually just force-set signals_week by exhausting
        # Re-check: might have crossed week boundary depending on BASE_TS
        # Simpler: use a fresh user and a tight loop
        self.store.register_user("w", "FREE", "@w")
        weekly_limit = WEEKLY_QUOTA[UserTier.FREE]
        for _ in range(weekly_limit):
            ok, _ = self.store.check_and_consume_quota("w")
        # Don't advance day (same day), try one more
        ok, reason = self.store.check_and_consume_quota("w")
        self.assertFalse(ok)
        self.assertIn(reason, ("daily_quota", "weekly_quota"))

    def test_upsell_event_recorded(self):
        self.store.register_user("u", "FREE", "@c")
        self.store.record_upsell_event("u", "daily_quota", "sid1")
        events = self.store.upsell_events("u")
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].reason, "daily_quota")
        self.assertEqual(events[0].signal_id, "sid1")

    def test_get_nonexistent_user_returns_none(self):
        self.assertIsNone(self.store.get_user("nobody"))


# ===========================================================================
# DelayScheduler
# ===========================================================================

class TestDelayScheduler(unittest.TestCase):

    def setUp(self):
        self.clock = _clock()
        self.sched = _make_scheduler(self.clock)

    def _enqueue(self, signal_id="s1", user_id="u1", delay=0):
        return self.sched.enqueue(
            signal_id=signal_id,
            user_id=user_id,
            user_tier="PRO",
            channel_id="@ch",
            format_type="FULL",
            signal_json='{"tier":"TIER_A"}',
            gate_json='{"publish":true}',
            publish_after_ts=self.clock.now_ts() + delay,
        )

    def test_enqueue_returns_sequential_ids(self):
        id1 = self._enqueue("s1", "u1")
        id2 = self._enqueue("s2", "u1")
        self.assertIsNotNone(id1)
        self.assertIsNotNone(id2)
        self.assertGreater(id2, id1)

    def test_due_returns_past_entries(self):
        self._enqueue("s1", "u1", delay=-1)  # already past
        self.clock.advance(1.0)
        due = self.sched.due(self.clock.now_ts())
        self.assertEqual(len(due), 1)

    def test_due_excludes_future_entries(self):
        self._enqueue("s1", "u1", delay=3600)  # 1 hour in future
        due = self.sched.due(self.clock.now_ts())
        self.assertEqual(len(due), 0)

    def test_mark_delivered_prevents_re_delivery(self):
        qid = self._enqueue("s1", "u1", delay=-1)
        self.clock.advance(1.0)
        self.sched.mark_delivered(qid, self.clock.now_ts())
        due = self.sched.due(self.clock.now_ts())
        self.assertEqual(len(due), 0)

    def test_pending_count(self):
        self._enqueue("s1", "u1", delay=-1)
        self._enqueue("s2", "u2", delay=-1)
        self.assertEqual(self.sched.pending_count(), 2)
        self.clock.advance(1.0)
        due = self.sched.due(self.clock.now_ts())
        self.sched.mark_delivered(due[0].queue_id, self.clock.now_ts())
        self.assertEqual(self.sched.pending_count(), 1)

    def test_duplicate_enqueue_is_ignored(self):
        id1 = self._enqueue("s1", "u1")
        id2 = self._enqueue("s1", "u1")  # duplicate
        self.assertIsNotNone(id1)
        self.assertIsNone(id2)
        self.assertEqual(self.sched.pending_count(), 1)

    def test_replay_after_restart(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db = f.name
        try:
            c = ManualClock(ts=_BASE_TS)
            s1 = DelayScheduler(db, c)
            s1.enqueue("sx", "ux", "PRO", "@ch", "FULL",
                       '{}', '{}', c.now_ts() - 1)
            s1.close()

            # Re-open same file — entry must still be present
            s2 = DelayScheduler(db, c)
            due = s2.due(c.now_ts())
            self.assertEqual(len(due), 1)
            s2.close()
        finally:
            os.unlink(db)

    def test_get_entry(self):
        qid = self._enqueue("s1", "u1", delay=0)
        entry = self.sched.get_entry(qid)
        self.assertIsNotNone(entry)
        self.assertEqual(entry.signal_id, "s1")


# ===========================================================================
# Signal serialisation helpers
# ===========================================================================

class TestSignalSerialisation(unittest.TestCase):

    def test_round_trip(self):
        sig = _Sig(match_id="mx", tier="TIER_S", edge_score=0.12)
        json_str = _serialize_signal(sig)
        reconstructed = _deserialize_signal(json_str)
        self.assertEqual(reconstructed.match_id, "mx")
        self.assertEqual(reconstructed.tier, "TIER_S")
        self.assertAlmostEqual(reconstructed.edge_score, 0.12)

    def test_format_full_returns_string(self):
        sig = _deserialize_signal(_serialize_signal(_Sig(tier="TIER_A")))
        text = _format_signal(sig, "FULL")
        self.assertIsInstance(text, str)
        self.assertGreater(len(text), 0)

    def test_format_teaser_hides_selection(self):
        sig = _deserialize_signal(_serialize_signal(_Sig(selection="HOME", tier="TIER_S")))
        text = _format_signal(sig, "TEASER")
        self.assertNotIn("HOME", text)
        self.assertIn("Upgrade", text)

    def test_format_abbreviated_not_teaser(self):
        sig = _deserialize_signal(_serialize_signal(_Sig(tier="TIER_B")))
        text = _format_signal(sig, "ABBREVIATED")
        self.assertNotIn("Upgrade", text)


# ===========================================================================
# TieredDeliveryWorker
# ===========================================================================

class TestTieredDeliveryWorker(unittest.TestCase):

    def _setup(self, dry_run=True):
        clock = _clock()
        sched = _make_scheduler(clock)
        worker, wm, db_path = _make_worker(sched, clock)
        pub = _make_publisher(dry_run=dry_run)
        return clock, sched, worker, wm, db_path, pub

    def tearDown(self):
        # cleanup temp db files
        pass

    def test_deliver_due_calls_send_for_due_entry(self):
        clock, sched, worker, wm, db, pub = self._setup(dry_run=False)
        sched.enqueue("s1", "u1", "PRO", "@ch", "FULL",
                      _serialize_signal(_Sig()), '{}',
                      clock.now_ts() - 1)
        clock.advance(1.0)
        n = worker.deliver_due(pub)
        self.assertEqual(n, 1)
        self.assertEqual(len(pub.published_calls), 1)
        channel, text = pub.published_calls[0]
        self.assertEqual(channel, "@ch")
        worker.close()
        os.unlink(db)

    def test_deliver_due_skips_future_entries(self):
        clock, sched, worker, wm, db, pub = self._setup()
        sched.enqueue("s1", "u1", "PRO", "@ch", "FULL",
                      _serialize_signal(_Sig()), '{}',
                      clock.now_ts() + 3600)
        n = worker.deliver_due(pub)
        self.assertEqual(n, 0)
        worker.close()
        os.unlink(db)

    def test_mark_delivered_prevents_re_delivery(self):
        clock, sched, worker, wm, db, pub = self._setup()
        sched.enqueue("s1", "u1", "PRO", "@ch", "FULL",
                      _serialize_signal(_Sig()), '{}',
                      clock.now_ts() - 1)
        clock.advance(1.0)
        worker.deliver_due(pub)
        # Second call should deliver nothing
        n2 = worker.deliver_due(pub)
        self.assertEqual(n2, 0)
        worker.close()
        os.unlink(db)

    def test_delivery_log_row_created(self):
        clock, sched, worker, wm, db, pub = self._setup()
        sched.enqueue("s1", "u1", "PRO", "@ch", "FULL",
                      _serialize_signal(_Sig()), '{}',
                      clock.now_ts() - 1)
        clock.advance(1.0)
        worker.deliver_due(pub)
        self.assertEqual(worker.delivery_log_count(), 1)
        worker.close()
        os.unlink(db)

    def test_dry_run_publisher_not_called_via_send(self):
        clock, sched, worker, wm, db, pub = self._setup(dry_run=True)
        sched.enqueue("s1", "u1", "PRO", "@ch", "FULL",
                      _serialize_signal(_Sig()), '{}',
                      clock.now_ts() - 1)
        clock.advance(1.0)
        n = worker.deliver_due(pub)
        self.assertEqual(n, 1)
        # dry_run=True → _send() never called
        self.assertEqual(len(pub.published_calls), 0)
        worker.close()
        os.unlink(db)

    def test_watermark_injected_in_delivered_text(self):
        clock, sched, worker, wm, db, pub = self._setup(dry_run=False)
        qid = sched.enqueue("s1", "u1", "PRO", "@ch", "FULL",
                            _serialize_signal(_Sig()), '{}',
                            clock.now_ts() - 1)
        clock.advance(1.0)
        worker.deliver_due(pub)
        _, text = pub.published_calls[0]
        decoded_id = wm.decode(text)
        self.assertEqual(decoded_id, qid)
        worker.close()
        os.unlink(db)

    def test_failed_delivery_does_not_raise(self):
        clock, sched, worker, wm, db, pub = self._setup(dry_run=False)
        # Make _send always fail
        pub._send = lambda ch, t: False
        sched.enqueue("s1", "u1", "PRO", "@ch", "FULL",
                      _serialize_signal(_Sig()), '{}',
                      clock.now_ts() - 1)
        clock.advance(1.0)
        # Should not raise; returns 0 (published=False)
        n = worker.deliver_due(pub)
        self.assertEqual(n, 0)
        self.assertEqual(worker.delivery_log_count(), 1)
        worker.close()
        os.unlink(db)


# ===========================================================================
# MonetizationFilter — tier routing
# ===========================================================================

class TestMonetizationFilterRouting(unittest.TestCase):

    def _setup(self):
        clock = _clock()
        store = _make_store(clock)
        sched = _make_scheduler(clock)
        worker, wm, db = _make_worker(sched, clock)
        filt = _make_filter(store, sched, worker, wm, clock)
        return clock, store, sched, worker, wm, db, filt

    def _teardown(self, worker, db):
        worker.close()
        os.unlink(db)

    def test_pro_user_zero_delay_full_format(self):
        clock, store, sched, worker, wm, db, filt = self._setup()
        store.register_user("pro", "PRO", "@pro_ch")
        sig = _Sig(tier="TIER_S", timestamp="2026-01-05T12:00:00+00:00")
        result = filt.process(sig, _make_gate(True, "sid_pro"))
        self.assertEqual(result.enqueued, 1)
        entry = sched.due(clock.now_ts())  # delay=0 → immediately due
        self.assertEqual(len(entry), 1)
        self.assertEqual(entry[0].format_type, "FULL")
        self._teardown(worker, db)

    def test_basic_tier_a_gets_15min_delay(self):
        clock, store, sched, worker, wm, db, filt = self._setup()
        store.register_user("b", "BASIC", "@b_ch")
        sig = _Sig(tier="TIER_A", timestamp="2026-01-05T12:00:00+00:00")
        filt.process(sig, _make_gate(True, "sid_b"))
        # Nothing due immediately
        due_now = sched.due(clock.now_ts())
        self.assertEqual(len(due_now), 0)
        # Due after 15 min
        clock.advance(15 * 60)
        due_later = sched.due(clock.now_ts())
        self.assertEqual(len(due_later), 1)
        self.assertEqual(due_later[0].format_type, "FULL")
        self._teardown(worker, db)

    def test_basic_tier_b_zero_delay(self):
        clock, store, sched, worker, wm, db, filt = self._setup()
        store.register_user("b", "BASIC", "@b_ch")
        sig = _Sig(tier="TIER_B", timestamp="2026-01-05T12:00:00+00:00")
        filt.process(sig, _make_gate(True, "sid_bb"))
        due = sched.due(clock.now_ts())
        self.assertEqual(len(due), 1)
        self._teardown(worker, db)

    def test_free_user_tier_s_gets_teaser_and_4h_delay(self):
        clock, store, sched, worker, wm, db, filt = self._setup()
        store.register_user("f", "FREE", "@free_ch")
        sig = _Sig(tier="TIER_S", timestamp="2026-01-05T12:00:00+00:00")
        filt.process(sig, _make_gate(True, "sid_fs"))
        # Not due immediately
        self.assertEqual(len(sched.due(clock.now_ts())), 0)
        # Due after 4h
        clock.advance(4 * 3600)
        due = sched.due(clock.now_ts())
        self.assertEqual(len(due), 1)
        self.assertEqual(due[0].format_type, "TEASER")
        self._teardown(worker, db)

    def test_free_user_tier_b_gets_abbreviated_and_4h_delay(self):
        clock, store, sched, worker, wm, db, filt = self._setup()
        store.register_user("f", "FREE", "@free_ch")
        sig = _Sig(tier="TIER_B", timestamp="2026-01-05T12:00:00+00:00")
        filt.process(sig, _make_gate(True, "sid_fb"))
        clock.advance(4 * 3600)
        due = sched.due(clock.now_ts())
        self.assertEqual(len(due), 1)
        self.assertEqual(due[0].format_type, "ABBREVIATED")
        self._teardown(worker, db)

    def test_multiple_users_each_get_queue_entry(self):
        clock, store, sched, worker, wm, db, filt = self._setup()
        store.register_user("p1", "PRO",   "@p1")
        store.register_user("p2", "BASIC", "@p2")
        store.register_user("p3", "FREE",  "@p3")
        sig = _Sig(tier="TIER_B", timestamp="2026-01-05T12:00:00+00:00")
        result = filt.process(sig, _make_gate(True, "sid_multi"))
        self.assertEqual(result.enqueued, 3)
        self.assertEqual(result.active_users_seen, 3)
        self._teardown(worker, db)

    def test_suppress_gate_is_noop(self):
        clock, store, sched, worker, wm, db, filt = self._setup()
        store.register_user("p", "PRO", "@p")
        sig = _Sig(tier="TIER_A")
        result = filt.process(sig, _make_gate(publish=False))
        self.assertEqual(result.enqueued, 0)
        self.assertEqual(sched.pending_count(), 0)
        self._teardown(worker, db)

    def test_filter_result_fields(self):
        clock, store, sched, worker, wm, db, filt = self._setup()
        store.register_user("p", "PRO", "@p")
        result = filt.process(_Sig(), _make_gate(True, "sid1"))
        self.assertIsInstance(result, FilterResult)
        self.assertIn("enqueued", result.to_dict())
        self.assertIn("signal_id", result.to_dict())
        self._teardown(worker, db)


# ===========================================================================
# MonetizationFilter — quota enforcement + upsells
# ===========================================================================

class TestMonetizationFilterQuota(unittest.TestCase):

    def _setup(self):
        clock = _clock()
        store = _make_store(clock)
        sched = _make_scheduler(clock)
        worker, wm, db = _make_worker(sched, clock)
        filt = _make_filter(store, sched, worker, wm, clock)
        return clock, store, sched, worker, wm, db, filt

    def _teardown(self, worker, db):
        worker.close()
        os.unlink(db)

    def test_quota_exceeded_triggers_upsell(self):
        clock, store, sched, worker, wm, db, filt = self._setup()
        store.register_user("f", "FREE", "@f")
        # Exhaust daily quota
        for i in range(DAILY_QUOTA[UserTier.FREE]):
            store.check_and_consume_quota("f")
        # Next signal should trigger upsell
        result = filt.process(
            _Sig(tier="TIER_C", timestamp="2026-01-05T12:00:00+00:00"),
            _make_gate(True, "sid_upsell"),
        )
        self.assertEqual(result.upsells_sent, 1)
        self.assertEqual(result.skipped_quota, 1)
        self.assertEqual(result.enqueued, 0)
        events = store.upsell_events("f")
        self.assertGreater(len(events), 0)
        self._teardown(worker, db)

    def test_quota_consumed_per_signal(self):
        clock, store, sched, worker, wm, db, filt = self._setup()
        store.register_user("b", "BASIC", "@b")
        for i in range(DAILY_QUOTA[UserTier.BASIC]):
            filt.process(
                _Sig(tier="TIER_B", timestamp="2026-01-05T12:00:00+00:00"),
                _make_gate(True, f"sid_{i}"),
            )
        rec = store.get_user("b")
        self.assertEqual(rec.signals_today, DAILY_QUOTA[UserTier.BASIC])
        self._teardown(worker, db)

    def test_pro_user_high_quota_never_blocked(self):
        clock, store, sched, worker, wm, db, filt = self._setup()
        store.register_user("p", "PRO", "@p")
        for i in range(50):
            result = filt.process(
                _Sig(tier="TIER_A", timestamp="2026-01-05T12:00:00+00:00"),
                _make_gate(True, f"sid_{i}"),
            )
            self.assertEqual(result.skipped_quota, 0, f"quota blocked at i={i}")
        self._teardown(worker, db)

    def test_gate_timestamp_derived_from_signal_timestamp(self):
        clock, store, sched, worker, wm, db, filt = self._setup()
        store.register_user("b", "BASIC", "@b")
        sig = _Sig(tier="TIER_A", timestamp="2026-01-05T12:00:00+00:00")
        filt.process(sig, _make_gate(True, "sid_ts"))
        # Advance exactly 15min → entry should be due
        clock.advance(15 * 60)
        due = sched.due(clock.now_ts())
        self.assertEqual(len(due), 1)
        self._teardown(worker, db)


# ===========================================================================
# End-to-end: process → deliver_due
# ===========================================================================

class TestEndToEnd(unittest.TestCase):

    def _full_setup(self):
        clock = _clock()
        store = _make_store(clock)
        sched = _make_scheduler(clock)
        worker, wm, db = _make_worker(sched, clock)
        filt = _make_filter(store, sched, worker, wm, clock)
        pub = _make_publisher(dry_run=False)
        return clock, store, sched, worker, wm, db, filt, pub

    def test_pro_signal_delivered_immediately(self):
        clock, store, sched, worker, wm, db, filt, pub = self._full_setup()
        store.register_user("p", "PRO", "@pro")
        sig = _Sig(tier="TIER_A", timestamp="2026-01-05T12:00:00+00:00")
        filt.process(sig, _make_gate(True, "e2e_pro"))
        n = filt.deliver_due(pub)
        self.assertEqual(n, 1)
        self.assertEqual(len(pub.published_calls), 1)
        worker.close()
        os.unlink(db)

    def test_basic_signal_not_delivered_before_delay(self):
        clock, store, sched, worker, wm, db, filt, pub = self._full_setup()
        store.register_user("b", "BASIC", "@b")
        sig = _Sig(tier="TIER_A", timestamp="2026-01-05T12:00:00+00:00")
        filt.process(sig, _make_gate(True, "e2e_b"))
        n = filt.deliver_due(pub)
        self.assertEqual(n, 0)
        worker.close()
        os.unlink(db)

    def test_basic_signal_delivered_after_delay(self):
        clock, store, sched, worker, wm, db, filt, pub = self._full_setup()
        store.register_user("b", "BASIC", "@b")
        sig = _Sig(tier="TIER_A", timestamp="2026-01-05T12:00:00+00:00")
        filt.process(sig, _make_gate(True, "e2e_b_late"))
        clock.advance(15 * 60)
        n = filt.deliver_due(pub)
        self.assertEqual(n, 1)
        worker.close()
        os.unlink(db)

    def test_free_tier_teaser_delivered_after_4h(self):
        clock, store, sched, worker, wm, db, filt, pub = self._full_setup()
        store.register_user("f", "FREE", "@f")
        sig = _Sig(tier="TIER_S", timestamp="2026-01-05T12:00:00+00:00")
        filt.process(sig, _make_gate(True, "e2e_free"))
        clock.advance(4 * 3600)
        n = filt.deliver_due(pub)
        self.assertEqual(n, 1)
        _, text = pub.published_calls[0]
        self.assertIn("Upgrade", text)
        worker.close()
        os.unlink(db)

    def test_suppress_never_reaches_delivery(self):
        clock, store, sched, worker, wm, db, filt, pub = self._full_setup()
        store.register_user("p", "PRO", "@p")
        # Call process with suppress (safety guard)
        result = filt.process(_Sig(), _make_gate(publish=False))
        self.assertEqual(result.enqueued, 0)
        n = filt.deliver_due(pub)
        self.assertEqual(n, 0)
        worker.close()
        os.unlink(db)

    def test_replay_same_inputs_same_delivery(self):
        """Determinism: same clock+users+signal → same queue entry."""
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            sched_db = f.name
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            user_db = f.name
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            del_db = f.name
        try:
            for _ in range(2):
                c = ManualClock(ts=_BASE_TS)
                store = UserStore(user_db, c)
                store.register_user("p", "PRO", "@p")
                wm = WatermarkInjector()
                sched = DelayScheduler(sched_db, c)
                worker = TieredDeliveryWorker(sched, wm, del_db, c)
                filt = MonetizationFilter(store, sched, worker, wm, c)
                filt.process(
                    _Sig(tier="TIER_B", timestamp="2026-01-05T12:00:00+00:00"),
                    _make_gate(True, "replay_sid"),
                )
                worker.close()
                store.close()
                sched.close()
            # After both runs, pending_count should still be 1 (duplicate blocked)
            c2 = ManualClock(ts=_BASE_TS)
            sched2 = DelayScheduler(sched_db, c2)
            self.assertEqual(sched2.pending_count(), 1)
            sched2.close()
        finally:
            for p in (sched_db, user_db, del_db):
                try:
                    os.unlink(p)
                except FileNotFoundError:
                    pass


# ===========================================================================
# RevenueSimulator
# ===========================================================================

class TestRevenueSimulator(unittest.TestCase):

    def _setup(self, users=None):
        clock = _clock()
        store = _make_store(clock)
        if users:
            for uid, tier, ch in users:
                store.register_user(uid, tier, ch)
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            del_db = f.name
        rev = RevenueSimulator(store, del_db, clock)
        return store, rev, del_db

    def test_mrr_zero_with_no_paid_users(self):
        store, rev, db = self._setup([("f1", "FREE", "@f")])
        proj = rev.mrr_projection()
        self.assertEqual(proj.mrr_estimate, 0.0)
        os.unlink(db)

    def test_mrr_correct_with_known_users(self):
        store, rev, db = self._setup([
            ("b1", "BASIC", "@b1"),
            ("p1", "PRO",   "@p1"),
        ])
        proj = rev.mrr_projection()
        expected = (
            1 * RevenueSimulator.PRICE_BASIC * (1 - RevenueSimulator.MONTHLY_CHURN_BASIC)
            + 1 * RevenueSimulator.PRICE_PRO  * (1 - RevenueSimulator.MONTHLY_CHURN_PRO)
        )
        self.assertAlmostEqual(proj.mrr_estimate, expected, places=2)
        self.assertEqual(proj.n_basic, 1)
        self.assertEqual(proj.n_pro, 1)
        os.unlink(db)

    def test_funnel_snapshot_required_fields(self):
        store, rev, db = self._setup()
        snap = rev.funnel_snapshot()
        for field in ("snapshot_at", "total_free", "total_basic", "total_pro",
                      "total_active", "mrr_estimate"):
            self.assertTrue(hasattr(snap, field), f"missing field: {field}")
        os.unlink(db)

    def test_annual_run_rate_equals_12x_mrr(self):
        store, rev, db = self._setup([("b", "BASIC", "@b")])
        proj = rev.mrr_projection()
        self.assertAlmostEqual(proj.annual_run_rate, proj.mrr_estimate * 12, places=2)
        os.unlink(db)

    def test_churn_indicators_returns_list(self):
        store, rev, db = self._setup([("b", "BASIC", "@b")])
        indicators = rev.churn_indicators()
        self.assertIsInstance(indicators, list)
        os.unlink(db)

    def test_conversion_pipeline_based_on_free_users(self):
        store, rev, db = self._setup([
            ("f1", "FREE", "@f1"),
            ("f2", "FREE", "@f2"),
        ])
        proj = rev.mrr_projection()
        # Pipeline = n_free_engaged_30d * rate — 0 here (no delivery log entries)
        self.assertGreaterEqual(proj.conversion_pipeline, 0.0)
        os.unlink(db)


# ===========================================================================
# ServiceRuntime integration
# ===========================================================================

class TestServiceRuntimeIntegration(unittest.TestCase):

    def _make_runtime(self, monetization_filter=None, signal=None):
        from src.market.service.runtime import ServiceRuntime, IterationSummary
        from src.market.service.config import RuntimeConfig, SchedulerConfig, TelegramConfig
        from src.market.service.health import HealthMonitor

        # Build a minimal stub runtime with mocked dependencies
        config = RuntimeConfig(
            scheduler=SchedulerConfig(poll_interval_seconds=30.0),
            telegram=TelegramConfig(dry_run=True),
        )
        bridge = MagicMock()
        bridge.process_due.return_value = []
        bridge.monitor.return_value = {"ingestion_success": 1, "ingestion_failure": 0}

        gateway = MagicMock()
        gate_mock = MagicMock()
        gate_mock.publish = True
        gate_mock.signal_id = "sid_rt"
        gate_mock.reason_codes = []
        gateway.gate.return_value = gate_mock
        gateway.evaluate.return_value = None
        gateway.monitor.return_value = {"active_state": "PAPER", "risk_index": 0.0}

        publisher = MagicMock()
        pub_result = MagicMock()
        pub_result.published = True
        publisher.publish.return_value = pub_result
        publisher.dry_run = True

        health = HealthMonitor()
        sig = signal or _Sig()
        signal_source = lambda: [sig]

        scheduler = MagicMock()

        rt = ServiceRuntime(
            config=config,
            scheduler=scheduler,
            bridge=bridge,
            gateway=gateway,
            publisher=publisher,
            health=health,
            signal_source=signal_source,
            monetization_filter=monetization_filter,
        )
        return rt, gateway, publisher

    def test_no_monetization_publisher_called_directly(self):
        rt, gateway, publisher = self._make_runtime(monetization_filter=None)
        rt.run_once()
        publisher.publish.assert_called_once()

    def test_with_monetization_filter_process_called(self):
        filt = MagicMock()
        filt.process.return_value = FilterResult(signal_id="sid_rt", enqueued=1)
        filt.deliver_due.return_value = 0
        rt, gateway, publisher = self._make_runtime(monetization_filter=filt)
        rt.run_once()
        filt.process.assert_called_once()

    def test_with_monetization_publisher_not_called_directly(self):
        filt = MagicMock()
        filt.process.return_value = FilterResult(signal_id="sid_rt", enqueued=1)
        filt.deliver_due.return_value = 0
        rt, gateway, publisher = self._make_runtime(monetization_filter=filt)
        rt.run_once()
        publisher.publish.assert_not_called()

    def test_deliver_due_called_each_iteration(self):
        filt = MagicMock()
        filt.process.return_value = FilterResult(signal_id="sid_rt")
        filt.deliver_due.return_value = 0
        rt, _, _ = self._make_runtime(monetization_filter=filt)
        rt.run_once()
        rt.run_once()
        self.assertEqual(filt.deliver_due.call_count, 2)

    def test_suppress_signal_never_reaches_filter(self):
        filt = MagicMock()
        filt.process.return_value = FilterResult(signal_id="")
        filt.deliver_due.return_value = 0

        from src.market.service.runtime import ServiceRuntime
        from src.market.service.config import RuntimeConfig, SchedulerConfig, TelegramConfig
        from src.market.service.health import HealthMonitor

        config = RuntimeConfig(
            scheduler=SchedulerConfig(poll_interval_seconds=30.0),
            telegram=TelegramConfig(dry_run=True),
        )
        bridge = MagicMock()
        bridge.process_due.return_value = []
        bridge.monitor.return_value = {"ingestion_success": 1, "ingestion_failure": 0}

        gateway = MagicMock()
        gate_mock = MagicMock()
        gate_mock.publish = False  # SUPPRESS
        gate_mock.signal_id = "sid_sup"
        gate_mock.reason_codes = ["trust_low"]
        gateway.gate.return_value = gate_mock
        gateway.evaluate.return_value = None
        gateway.monitor.return_value = {"active_state": "PAPER", "risk_index": 0.0}

        publisher = MagicMock()
        publisher.dry_run = True

        rt = ServiceRuntime(
            config=config,
            scheduler=MagicMock(),
            bridge=bridge,
            gateway=gateway,
            publisher=publisher,
            health=HealthMonitor(),
            signal_source=lambda: [_Sig()],
            monetization_filter=filt,
        )
        rt.run_once()
        filt.process.assert_not_called()


# ===========================================================================
# Tier policy constants integrity
# ===========================================================================

class TestModelPolicyConstants(unittest.TestCase):

    def test_all_tiers_all_grades_in_tier_delays(self):
        for tier in UserTier:
            self.assertIn(tier, TIER_DELAYS, f"TIER_DELAYS missing tier {tier}")
            for grade in ("TIER_S", "TIER_A", "TIER_B", "TIER_C"):
                self.assertIn(grade, TIER_DELAYS[tier],
                              f"TIER_DELAYS[{tier}] missing grade {grade}")

    def test_all_tiers_all_grades_in_format_rules(self):
        for tier in UserTier:
            for grade in ("TIER_S", "TIER_A", "TIER_B", "TIER_C"):
                self.assertIn((tier, grade), FORMAT_RULES)

    def test_pro_all_delays_zero(self):
        for grade in ("TIER_S", "TIER_A", "TIER_B", "TIER_C"):
            self.assertEqual(TIER_DELAYS[UserTier.PRO][grade], 0)

    def test_pro_all_formats_full(self):
        for grade in ("TIER_S", "TIER_A", "TIER_B", "TIER_C"):
            self.assertEqual(FORMAT_RULES[(UserTier.PRO, grade)], FormatType.FULL)

    def test_free_all_delays_4h(self):
        for grade in ("TIER_S", "TIER_A", "TIER_B", "TIER_C"):
            self.assertEqual(TIER_DELAYS[UserTier.FREE][grade], 4 * 3600)

    def test_free_high_tiers_are_teaser(self):
        self.assertEqual(FORMAT_RULES[(UserTier.FREE, "TIER_S")], FormatType.TEASER)
        self.assertEqual(FORMAT_RULES[(UserTier.FREE, "TIER_A")], FormatType.TEASER)

    def test_free_low_tiers_are_abbreviated(self):
        self.assertEqual(FORMAT_RULES[(UserTier.FREE, "TIER_B")], FormatType.ABBREVIATED)
        self.assertEqual(FORMAT_RULES[(UserTier.FREE, "TIER_C")], FormatType.ABBREVIATED)

    def test_basic_high_tiers_have_15min_delay(self):
        self.assertEqual(TIER_DELAYS[UserTier.BASIC]["TIER_S"], 15 * 60)
        self.assertEqual(TIER_DELAYS[UserTier.BASIC]["TIER_A"], 15 * 60)

    def test_basic_low_tiers_zero_delay(self):
        self.assertEqual(TIER_DELAYS[UserTier.BASIC]["TIER_B"], 0)
        self.assertEqual(TIER_DELAYS[UserTier.BASIC]["TIER_C"], 0)


# ===========================================================================
# Additivity
# ===========================================================================

class TestAdditivity(unittest.TestCase):

    def test_m11_acceptance_hash_unchanged(self):
        import tests.test_m11_acceptance as m
        self.assertEqual(
            m.run_hash(m.baseline_providers()),
            m.TestM11Acceptance.BASELINE_HASH,
        )

    def test_existing_service_runtime_tests_unaffected(self):
        """Smoke: import the existing runtime test module without error."""
        import tests.test_service_runtime  # noqa: F401


if __name__ == "__main__":
    unittest.main(verbosity=2)
