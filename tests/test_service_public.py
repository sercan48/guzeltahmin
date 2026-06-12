"""PHASE-LIVE L7 — Offline deterministic tests for the public launch layer.

No network. ManualClock for all time. tempfile for all I/O.
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from dataclasses import dataclass
from types import SimpleNamespace
from typing import List, Optional

from src.market.service.monetization.clock import ManualClock
from src.market.service.monetization.worker import (
    _format_signal, _serialize_signal, _deserialize_signal,
)
from src.market.service.public.dashboard import DashboardExporter
from src.market.service.public.launch import (
    LaunchValidationHarness, LeakageError, PublicPublisher,
    assert_no_leakage, build_public_launch,
)
from src.market.service.public.metrics import (
    PublicDeliveryMetrics, SubscriberTracker,
)
from src.market.service.public.profile import PublicChannelProfile


_BASE_TS = 1767614400.0  # 2026-01-05 12:00:00 UTC


@dataclass
class _Sig:
    match_id: str = "m1"
    market: str = "1X2"
    selection: str = "HOME"
    tier: str = "TIER_S"
    edge_score: float = 0.05
    confidence: float = 0.80
    truth_confidence: float = 0.75
    timestamp: str = "2026-01-05T12:00:00+00:00"


def _gate(publish: bool = True) -> SimpleNamespace:
    return SimpleNamespace(publish=publish, signal_id="sid")


def _clock() -> ManualClock:
    return ManualClock(ts=_BASE_TS)


# ===========================================================================
# PublicChannelProfile
# ===========================================================================

class TestPublicChannelProfile(unittest.TestCase):

    def test_dry_run_default_true(self):
        self.assertTrue(PublicChannelProfile().dry_run)

    def test_teaser_only_default_true(self):
        self.assertTrue(PublicChannelProfile().teaser_only)

    def test_free_tier_s_maps_to_teaser(self):
        p = PublicChannelProfile()
        self.assertEqual(p.allowed_format_for_grade("TIER_S"), "TEASER")

    def test_free_tier_a_maps_to_teaser(self):
        p = PublicChannelProfile()
        self.assertEqual(p.allowed_format_for_grade("TIER_A"), "TEASER")

    def test_free_tier_b_maps_to_abbreviated(self):
        p = PublicChannelProfile()
        self.assertEqual(p.allowed_format_for_grade("TIER_B"), "ABBREVIATED")

    def test_never_maps_to_full(self):
        p = PublicChannelProfile()
        for grade in ("TIER_S", "TIER_A", "TIER_B", "TIER_C"):
            self.assertNotEqual(p.allowed_format_for_grade(grade), "FULL")

    def test_high_tier_only_publishes_s_and_a(self):
        p = PublicChannelProfile(publish_high_tier_only=True)
        self.assertTrue(p.is_publishable("TIER_S"))
        self.assertTrue(p.is_publishable("TIER_A"))
        self.assertFalse(p.is_publishable("TIER_B"))
        self.assertFalse(p.is_publishable("TIER_C"))

    def test_all_tiers_publishable_when_flag_off(self):
        p = PublicChannelProfile(publish_high_tier_only=False)
        for grade in ("TIER_S", "TIER_A", "TIER_B", "TIER_C"):
            self.assertTrue(p.is_publishable(grade))

    def test_disabled_profile_publishes_nothing(self):
        p = PublicChannelProfile(enabled=False)
        self.assertFalse(p.is_publishable("TIER_S"))

    def test_validate_passes_default(self):
        PublicChannelProfile().validate()

    def test_validate_rejects_non_teaser_only(self):
        with self.assertRaises(ValueError):
            PublicChannelProfile(teaser_only=False).validate()

    def test_validate_requires_channel_when_live(self):
        with self.assertRaises(ValueError):
            PublicChannelProfile(dry_run=False, channel_id="").validate()

    def test_validate_rejects_bad_cap(self):
        with self.assertRaises(ValueError):
            PublicChannelProfile(max_publications_per_day=0).validate()


# ===========================================================================
# Leakage guard
# ===========================================================================

class TestLeakageGuard(unittest.TestCase):

    def test_teaser_text_passes(self):
        sig = _deserialize_signal(_serialize_signal(_Sig(tier="TIER_S")))
        text = _format_signal(sig, "TEASER")
        assert_no_leakage(text)  # must not raise

    def test_full_text_raises(self):
        sig = _deserialize_signal(_serialize_signal(_Sig(tier="TIER_S")))
        full = _format_signal(sig, "FULL")
        with self.assertRaises(LeakageError):
            assert_no_leakage(full)

    def test_vip_marker_detected(self):
        with self.assertRaises(LeakageError):
            assert_no_leakage("🔒 VIP SIGNAL — TIER_S")

    def test_truth_confidence_marker_detected(self):
        with self.assertRaises(LeakageError):
            assert_no_leakage("Truth Confidence: 75%")

    def test_edge_score_marker_detected(self):
        with self.assertRaises(LeakageError):
            assert_no_leakage("Edge Score: 5%")

    def test_plain_text_passes(self):
        assert_no_leakage("Upgrade to PRO for real-time signals")


# ===========================================================================
# PublicPublisher
# ===========================================================================

class TestPublicPublisher(unittest.TestCase):

    def _make(self, profile=None, send_fn=None):
        prof = profile or PublicChannelProfile(channel_id="@free")
        metrics = PublicDeliveryMetrics()
        pub = PublicPublisher(prof, metrics, clock=_clock(), send_fn=send_fn)
        return pub, metrics, prof

    def test_publishes_tier_s_teaser(self):
        pub, metrics, _ = self._make()
        res = pub.publish(_Sig(tier="TIER_S"), _gate(True))
        self.assertTrue(res["published"])
        self.assertEqual(res["format"], "TEASER")

    def test_suppressed_gate_not_published(self):
        pub, metrics, _ = self._make()
        res = pub.publish(_Sig(tier="TIER_S"), _gate(publish=False))
        self.assertFalse(res["published"])
        self.assertEqual(res["reason"], "gate_suppressed")

    def test_low_tier_not_published_by_default(self):
        pub, metrics, _ = self._make()
        res = pub.publish(_Sig(tier="TIER_C"), _gate(True))
        self.assertFalse(res["published"])
        self.assertEqual(res["reason"], "not_publishable_grade")

    def test_impression_recorded_even_when_suppressed(self):
        pub, metrics, _ = self._make()
        pub.publish(_Sig(tier="TIER_S"), _gate(publish=False))
        self.assertEqual(metrics.snapshot().impressions, 1)

    def test_delivery_recorded_on_publish(self):
        pub, metrics, _ = self._make()
        pub.publish(_Sig(tier="TIER_S"), _gate(True))
        snap = metrics.snapshot()
        self.assertEqual(snap.delivered, 1)
        self.assertEqual(snap.teasers, 1)

    def test_daily_cap_enforced(self):
        prof = PublicChannelProfile(channel_id="@free", max_publications_per_day=2)
        pub, metrics, _ = self._make(profile=prof)
        for _ in range(3):
            pub.publish(_Sig(tier="TIER_S"), _gate(True))
        self.assertEqual(metrics.snapshot().delivered, 2)

    def test_daily_cap_resets_next_day(self):
        prof = PublicChannelProfile(channel_id="@free", max_publications_per_day=1)
        clock = _clock()
        metrics = PublicDeliveryMetrics()
        pub = PublicPublisher(prof, metrics, clock=clock)
        pub.publish(_Sig(tier="TIER_S"), _gate(True))
        pub.publish(_Sig(tier="TIER_S"), _gate(True))  # capped
        self.assertEqual(metrics.snapshot().delivered, 1)
        clock.advance(86401.0)  # next day
        pub.publish(_Sig(tier="TIER_S"), _gate(True))
        self.assertEqual(metrics.snapshot().delivered, 2)

    def test_live_send_invoked_when_not_dry_run(self):
        sent = []
        prof = PublicChannelProfile(channel_id="@free", dry_run=False)
        pub, metrics, _ = self._make(
            profile=prof, send_fn=lambda ch, t: sent.append((ch, t)) or True
        )
        pub.publish(_Sig(tier="TIER_S"), _gate(True))
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0][0], "@free")

    def test_live_send_text_is_teaser_no_leakage(self):
        sent = []
        prof = PublicChannelProfile(channel_id="@free", dry_run=False)
        pub, metrics, _ = self._make(
            profile=prof, send_fn=lambda ch, t: sent.append(t) or True
        )
        pub.publish(_Sig(tier="TIER_S"), _gate(True))
        # text must not contain leakage markers
        assert_no_leakage(sent[0])
        self.assertIn("Upgrade", sent[0])

    def test_dry_run_does_not_call_send_fn(self):
        called = []
        prof = PublicChannelProfile(channel_id="@free", dry_run=True)
        pub, metrics, _ = self._make(
            profile=prof, send_fn=lambda ch, t: called.append(1) or True
        )
        pub.publish(_Sig(tier="TIER_S"), _gate(True))
        self.assertEqual(len(called), 0)


# ===========================================================================
# PublicDeliveryMetrics
# ===========================================================================

class TestPublicDeliveryMetrics(unittest.TestCase):

    def test_impressions_counted(self):
        m = PublicDeliveryMetrics()
        m.record_impression()
        m.record_impression()
        self.assertEqual(m.snapshot().impressions, 2)

    def test_delivery_rate(self):
        m = PublicDeliveryMetrics()
        m.record_impression()
        m.record_impression()
        m.record_delivery("TEASER", latency_ms=10.0)
        self.assertAlmostEqual(m.snapshot().delivery_rate, 0.5, places=3)

    def test_teaser_and_abbreviated_counted(self):
        m = PublicDeliveryMetrics()
        m.record_delivery("TEASER")
        m.record_delivery("ABBREVIATED")
        m.record_delivery("TEASER")
        snap = m.snapshot()
        self.assertEqual(snap.teasers, 2)
        self.assertEqual(snap.abbreviated, 1)

    def test_latency_percentiles(self):
        m = PublicDeliveryMetrics()
        for v in (10.0, 20.0, 30.0, 40.0, 50.0):
            m.record_delivery("TEASER", latency_ms=v)
        snap = m.snapshot()
        self.assertIsNotNone(snap.publication_latency_p50_ms)
        self.assertGreaterEqual(snap.publication_latency_p95_ms,
                                snap.publication_latency_p50_ms)

    def test_latency_none_when_no_delivery(self):
        m = PublicDeliveryMetrics()
        self.assertIsNone(m.snapshot().publication_latency_p50_ms)

    def test_suppressed_non_public_counted(self):
        m = PublicDeliveryMetrics()
        m.record_suppressed_non_public()
        self.assertEqual(m.snapshot().suppressed_non_public, 1)

    def test_snapshot_to_dict(self):
        m = PublicDeliveryMetrics()
        m.record_impression()
        d = m.snapshot().to_dict()
        for key in ("impressions", "delivered", "teasers", "delivery_rate"):
            self.assertIn(key, d)


# ===========================================================================
# SubscriberTracker
# ===========================================================================

class TestSubscriberTracker(unittest.TestCase):

    def test_add_subscriber(self):
        t = SubscriberTracker(clock=_clock())
        t.add_subscriber("u1")
        t.add_subscriber("u2")
        self.assertEqual(t.snapshot().total_free, 2)

    def test_remove_subscriber(self):
        t = SubscriberTracker(clock=_clock())
        t.add_subscriber("u1")
        t.remove_subscriber("u1")
        self.assertEqual(t.snapshot().total_free, 0)

    def test_engagement_counted(self):
        t = SubscriberTracker(clock=_clock())
        t.record_engagement("u1")
        t.record_engagement("u1")
        snap = t.snapshot()
        self.assertEqual(snap.total_engagements, 2)
        self.assertEqual(snap.unique_engaged, 1)

    def test_engagement_adds_subscriber(self):
        t = SubscriberTracker(clock=_clock())
        t.record_engagement("u1")
        self.assertEqual(t.snapshot().total_free, 1)

    def test_active_within_window(self):
        clock = _clock()
        t = SubscriberTracker(clock=clock, active_window_seconds=86400.0)
        t.record_engagement("u1")
        self.assertEqual(t.snapshot().active_free, 1)

    def test_inactive_outside_window(self):
        clock = _clock()
        t = SubscriberTracker(clock=clock, active_window_seconds=86400.0)
        t.record_engagement("u1")
        clock.advance(86400.0 * 2)  # 2 days later
        self.assertEqual(t.snapshot().active_free, 0)

    def test_engagement_rate(self):
        t = SubscriberTracker(clock=_clock())
        t.add_subscriber("u1")
        t.add_subscriber("u2")
        t.record_engagement("u1")
        self.assertAlmostEqual(t.snapshot().engagement_rate, 0.5, places=3)

    def test_snapshot_to_dict(self):
        t = SubscriberTracker(clock=_clock())
        t.add_subscriber("u1")
        d = t.snapshot().to_dict()
        for key in ("total_free", "active_free", "engagement_rate"):
            self.assertIn(key, d)


# ===========================================================================
# DashboardExporter
# ===========================================================================

class TestDashboardExporter(unittest.TestCase):

    def test_record_day_appends_trends(self):
        d = DashboardExporter()
        d.record_day("2026-06-12", 85.0, {"betfair": {}}, 10)
        self.assertEqual(len(d.readiness_trend), 1)
        self.assertEqual(len(d.signal_volume_trend), 1)
        self.assertEqual(len(d.provider_health_trend), 1)

    def test_trend_bounded(self):
        d = DashboardExporter(max_trend_points=3)
        for i in range(10):
            d.record_day(f"day-{i}", float(i), {}, i)
        self.assertEqual(len(d.readiness_trend), 3)

    def test_compile_snapshot_structure(self):
        d = DashboardExporter()
        snap = d.compile_snapshot(
            day="2026-06-12",
            public_delivery={"impressions": 5},
            subscribers={"total_free": 3},
            readiness={"overall": 80.0},
            operational={"uptime_seconds": 100},
            alerts={"total": 0},
        )
        for key in ("day", "mode", "public_delivery", "subscribers",
                    "readiness", "operational", "alerts", "trends"):
            self.assertIn(key, snap)
        self.assertEqual(snap["mode"], "PUBLIC_DRY_RUN")

    def test_export_json_creates_file(self):
        d = DashboardExporter()
        snap = d.compile_snapshot(
            day="2026-06-12", public_delivery={}, subscribers={},
            readiness={"overall": 0}, operational={}, alerts={},
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "public-2026-06-12.json")
            d.export_json(path, snap)
            self.assertTrue(os.path.exists(path))
            with open(path) as fh:
                self.assertEqual(json.load(fh)["mode"], "PUBLIC_DRY_RUN")

    def test_signal_volume_trend_values(self):
        d = DashboardExporter()
        d.record_day("d1", 90.0, {}, 42)
        self.assertEqual(d.signal_volume_trend[0]["signal_volume"], 42)


# ===========================================================================
# LaunchValidationHarness
# ===========================================================================

class TestLaunchValidationHarness(unittest.TestCase):

    def _make(self, profile=None, signal_source=None, report_dir=None):
        prof = profile or PublicChannelProfile(channel_id="@free")
        harness, comps = build_public_launch(
            prof, clock=_clock(),
            signal_source=signal_source,
            report_dir=report_dir or tempfile.mkdtemp(),
        )
        return harness, comps

    def test_run_iteration_publishes_teasers(self):
        sigs = [_Sig(tier="TIER_S"), _Sig(tier="TIER_A")]
        harness, comps = self._make(signal_source=lambda: sigs)
        result = harness.run_iteration()
        self.assertEqual(result["published"], 2)
        self.assertEqual(result["leaked_blocked"], 0)

    def test_run_iteration_with_explicit_signals(self):
        harness, comps = self._make()
        result = harness.run_iteration([_Sig(tier="TIER_S")])
        self.assertEqual(result["published"], 1)

    def test_low_tier_not_published(self):
        harness, comps = self._make()
        result = harness.run_iteration([_Sig(tier="TIER_C")])
        self.assertEqual(result["published"], 0)

    def test_verify_publication_rules_passes(self):
        harness, _ = self._make()
        sample = [_Sig(tier="TIER_S"), _Sig(tier="TIER_A")]
        res = harness.verify_publication_rules(sample)
        self.assertTrue(res["passed"])
        self.assertEqual(res["violations"], [])

    def test_verify_publication_rules_no_full_format(self):
        harness, _ = self._make()
        sample = [_Sig(tier=g) for g in ("TIER_S", "TIER_A", "TIER_B", "TIER_C")]
        res = harness.verify_publication_rules(sample)
        # No violations: every publishable grade resolves to teaser/abbreviated
        self.assertTrue(res["passed"])

    def test_export_dashboard_creates_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            harness, _ = self._make(report_dir=tmp)
            harness.run_iteration([_Sig(tier="TIER_S")])
            path = harness.export_dashboard(
                "2026-06-12",
                readiness={"overall": 85.0},
                operational={"provider_health": {}},
                alerts={"total": 0},
            )
            self.assertTrue(os.path.exists(path))
            with open(path) as fh:
                data = json.load(fh)
            self.assertEqual(data["mode"], "PUBLIC_DRY_RUN")

    def test_run_stops_at_max_iterations(self):
        calls = []
        harness, _ = self._make(signal_source=lambda: calls.append(1) or [])
        harness.run(max_iterations=3, poll_interval_seconds=0.001)
        self.assertEqual(len(calls), 3)

    def test_shutdown_sets_stop(self):
        harness, _ = self._make()
        harness.shutdown()
        self.assertTrue(harness._stop)

    def test_default_gate_treats_signals_as_allow(self):
        harness, _ = self._make()
        # No gate_fn injected → default publish=True
        result = harness.run_iteration([_Sig(tier="TIER_S")])
        self.assertEqual(result["published"], 1)

    def test_gate_fn_suppress_blocks_publish(self):
        prof = PublicChannelProfile(channel_id="@free")
        harness, comps = build_public_launch(
            prof, clock=_clock(),
            gate_fn=lambda sig: SimpleNamespace(publish=False, signal_id="x"),
            report_dir=tempfile.mkdtemp(),
        )
        result = harness.run_iteration([_Sig(tier="TIER_S")])
        self.assertEqual(result["published"], 0)


# ===========================================================================
# No-leakage end-to-end
# ===========================================================================

class TestNoLeakageEndToEnd(unittest.TestCase):

    def test_all_published_text_is_leak_free(self):
        sent_texts = []
        prof = PublicChannelProfile(channel_id="@free", dry_run=False,
                                    publish_high_tier_only=False)
        harness, comps = build_public_launch(
            prof, clock=_clock(),
            send_fn=lambda ch, t: sent_texts.append(t) or True,
            report_dir=tempfile.mkdtemp(),
        )
        # Throw every grade at it
        sigs = [_Sig(tier=g) for g in ("TIER_S", "TIER_A", "TIER_B", "TIER_C")]
        harness.run_iteration(sigs)
        self.assertGreater(len(sent_texts), 0)
        for text in sent_texts:
            assert_no_leakage(text)  # must not raise for any

    def test_replay_determinism_same_inputs_same_metrics(self):
        def run_once():
            harness, comps = build_public_launch(
                PublicChannelProfile(channel_id="@free"),
                clock=ManualClock(ts=_BASE_TS),
                report_dir=tempfile.mkdtemp(),
            )
            harness.run_iteration([_Sig(tier="TIER_S"), _Sig(tier="TIER_A")])
            return comps["delivery"].snapshot().to_dict()

        snap1 = run_once()
        snap2 = run_once()
        self.assertEqual(snap1, snap2)


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

    def test_existing_service_modules_import(self):
        import tests.test_service_runtime       # noqa: F401
        import tests.test_service_monetization  # noqa: F401
        import tests.test_service_deployment    # noqa: F401


if __name__ == "__main__":
    unittest.main(verbosity=2)
