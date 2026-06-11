"""PHASE-LIVE L3 — ServiceRuntime offline tests. No network, no Telegram calls."""

from __future__ import annotations

import tempfile
import time
import unittest
from dataclasses import dataclass
from typing import List, Optional
from unittest.mock import patch

from src.market.control import GateResult, ControlGateway, ControlPlane, ControlMetrics
from src.market.orchestration import PaperSignal
from src.market.service.config import RuntimeConfig, TelegramConfig, SchedulerConfig
from src.market.service.health import HealthMonitor
from src.market.service.publisher import TelegramPublisher, PublishResult, SignalFormatter
from src.market.service.runtime import ServiceRuntime, IterationSummary
from src.market.service.lock import SingleInstanceLock
from src.market.activation import JobResult, ProviderError


# ---------------------------------------------------------------------------
# Helpers / stubs
# ---------------------------------------------------------------------------

def _paper_signal(tier="TIER_B", truth_confidence=0.85) -> PaperSignal:
    return PaperSignal(
        match_id="epl_m1", market="1X2", selection="HOME",
        edge_score=0.042, tier=tier, confidence=0.87,
        truth_confidence=truth_confidence,
        timestamp="2026-06-11T15:00:00Z",
    )


def _gate(publish: bool, decision: str = "ALLOW") -> GateResult:
    return GateResult(
        signal_id="sig_001", state="PAPER", decision=decision,
        publish=publish, recorded=True, reason_codes=[decision],
    )


class _BridgeStub:
    def __init__(self, results: Optional[List] = None, raise_error: bool = False):
        self.results = results or []
        self.raise_error = raise_error
        self.process_calls = 0
        self.outcome_calls: List[str] = []

    def process_due(self) -> List:
        self.process_calls += 1
        if self.raise_error:
            raise ProviderError("simulated outage")
        return self.results

    def ingest_outcome(self, match_id: str) -> JobResult:
        self.outcome_calls.append(match_id)
        return JobResult(f"{match_id}:OUTCOME", "SUCCESS", 1, [])

    def monitor(self) -> dict:
        return {"ingestion_success": len(self.results), "ingestion_failure": 0}

    def close(self) -> None:
        pass


class _GatewayStub:
    def __init__(self, publish: bool = True, decision: str = "ALLOW"):
        self._publish = publish
        self._decision = decision
        self.evaluated: List[ControlMetrics] = []
        self.gated: List = []

    def evaluate(self, metrics: ControlMetrics) -> None:
        self.evaluated.append(metrics)

    def gate(self, signal, signal_id=None) -> GateResult:
        result = GateResult(
            signal_id=signal_id or "stub_sid",
            state="PAPER", decision=self._decision,
            publish=self._publish, recorded=True,
            reason_codes=[self._decision],
        )
        self.gated.append(result)
        return result

    def monitor(self) -> dict:
        return {"active_state": "PAPER", "risk_index": 0.12, "active_suppressions": []}

    def close(self) -> None:
        pass


class _PublisherSpy:
    def __init__(self, dry_run: bool = True):
        self.dry_run = dry_run
        self.published: List = []
        self.http_calls = 0

    def publish(self, signal, gate) -> PublishResult:
        if gate.publish:
            self.published.append((signal, gate))
            return PublishResult(gate.signal_id, True, "@test", self.dry_run, "dry_run")
        return PublishResult(gate.signal_id, False, None, self.dry_run, "suppressed")


def _runtime(
    signals=None,
    bridge=None,
    gateway=None,
    publisher=None,
    health=None,
    cfg=None,
) -> ServiceRuntime:
    cfg = cfg or RuntimeConfig(
        telegram=TelegramConfig(dry_run=True),
        scheduler=SchedulerConfig(poll_interval_seconds=1.0),
    )
    return ServiceRuntime(
        config=cfg,
        scheduler=None,             # scheduler called via bridge stub
        bridge=bridge or _BridgeStub(),
        gateway=gateway or _GatewayStub(),
        publisher=publisher or _PublisherSpy(),
        health=health or HealthMonitor(),
        signal_source=(lambda: signals) if signals is not None else None,
    )


# ---------------------------------------------------------------------------
# 1. Service loop — basic iteration
# ---------------------------------------------------------------------------

class TestServiceLoop(unittest.TestCase):

    def test_process_due_called_each_iteration(self):
        bridge = _BridgeStub()
        rt = _runtime(bridge=bridge)
        rt.run_once()
        rt.run_once()
        self.assertEqual(bridge.process_calls, 2)

    def test_no_signals_no_publish(self):
        spy = _PublisherSpy()
        rt = _runtime(signals=[], publisher=spy)
        rt.run_once()
        self.assertEqual(spy.published, [])

    def test_summary_counts_jobs(self):
        bridge = _BridgeStub(results=[
            JobResult("j1", "SUCCESS", 1, ["betfair"]),
            JobResult("j2", "FAILED", 2, [], "err"),
        ])
        s = _runtime(bridge=bridge).run_once()
        self.assertEqual(s.jobs_processed, 2)


# ---------------------------------------------------------------------------
# 2. Gating behaviour
# ---------------------------------------------------------------------------

class TestGatingBehaviour(unittest.TestCase):

    def test_allowed_signal_calls_publisher(self):
        spy = _PublisherSpy()
        gw = _GatewayStub(publish=True)
        rt = _runtime(signals=[_paper_signal()], gateway=gw, publisher=spy)
        s = rt.run_once()
        self.assertEqual(s.published, 1)
        self.assertEqual(s.suppressed, 0)
        self.assertEqual(len(spy.published), 1)

    def test_suppressed_signal_skips_publisher(self):
        spy = _PublisherSpy()
        gw = _GatewayStub(publish=False, decision="SUPPRESS")
        rt = _runtime(signals=[_paper_signal()], gateway=gw, publisher=spy)
        s = rt.run_once()
        self.assertEqual(s.suppressed, 1)
        self.assertEqual(s.published, 0)
        self.assertEqual(spy.published, [])

    def test_halt_decision_no_publish(self):
        spy = _PublisherSpy()
        gw = _GatewayStub(publish=False, decision="HALT")
        rt = _runtime(signals=[_paper_signal()], gateway=gw, publisher=spy)
        s = rt.run_once()
        self.assertEqual(s.published, 0)
        self.assertEqual(spy.published, [])

    def test_gateway_evaluate_called_with_metrics(self):
        gw = _GatewayStub()
        rt = _runtime(gateway=gw)
        rt.run_once()
        self.assertEqual(len(gw.evaluated), 1)
        self.assertIsInstance(gw.evaluated[0], ControlMetrics)


# ---------------------------------------------------------------------------
# 3. Provider outage → degraded mode
# ---------------------------------------------------------------------------

class TestDegradedMode(unittest.TestCase):

    def test_single_failure_not_yet_degraded(self):
        bridge = _BridgeStub(raise_error=True)
        cfg = RuntimeConfig(
            telegram=TelegramConfig(dry_run=True),
            scheduler=SchedulerConfig(),
            degraded_failure_threshold=3,
        )
        rt = _runtime(bridge=bridge, cfg=cfg)
        s = rt.run_once()
        self.assertFalse(s.degraded)

    def test_repeated_failures_enter_degraded(self):
        bridge = _BridgeStub(raise_error=True)
        cfg = RuntimeConfig(
            telegram=TelegramConfig(dry_run=True),
            scheduler=SchedulerConfig(),
            degraded_failure_threshold=2,
        )
        rt = _runtime(bridge=bridge, cfg=cfg)
        rt.run_once()
        s = rt.run_once()
        self.assertTrue(s.degraded)

    def test_recovery_clears_degraded(self):
        cfg = RuntimeConfig(
            telegram=TelegramConfig(dry_run=True),
            scheduler=SchedulerConfig(),
            degraded_failure_threshold=1,
        )

        class _ToggleBridge(_BridgeStub):
            def __init__(self):
                super().__init__()
                self._calls = 0
            def process_due(self):
                self._calls += 1
                self.process_calls += 1
                if self._calls == 1:
                    raise ProviderError("first call fails")
                return []

        rt = _runtime(bridge=_ToggleBridge(), cfg=cfg)
        s1 = rt.run_once()   # fails → degraded
        self.assertTrue(s1.degraded)
        s2 = rt.run_once()   # recovers
        self.assertFalse(s2.degraded)

    def test_degraded_mode_no_signals_published(self):
        spy = _PublisherSpy()
        cfg = RuntimeConfig(
            telegram=TelegramConfig(dry_run=True),
            scheduler=SchedulerConfig(),
            degraded_failure_threshold=1,
        )
        rt = _runtime(
            signals=[_paper_signal()],
            bridge=_BridgeStub(raise_error=True),
            publisher=spy, cfg=cfg,
        )
        rt.run_once()
        self.assertEqual(spy.published, [])


# ---------------------------------------------------------------------------
# 4. Dry-run mode — no HTTP
# ---------------------------------------------------------------------------

class TestDryRun(unittest.TestCase):

    def test_dry_run_publisher_no_urlopen(self):
        pub = TelegramPublisher(
            bot_token="tok", vip_channel_id="@vip", standard_channel_id="@std",
            dry_run=True,
        )
        gate = _gate(publish=True)
        with patch("urllib.request.urlopen") as mock_open:
            result = pub.publish(_paper_signal(), gate)
            mock_open.assert_not_called()
        self.assertTrue(result.dry_run)
        self.assertTrue(result.published)

    def test_live_mode_publisher_calls_urlopen(self):
        pub = TelegramPublisher(
            bot_token="tok", vip_channel_id="@vip", standard_channel_id="@std",
            dry_run=False,
        )
        gate = _gate(publish=True)
        from unittest.mock import MagicMock
        mock_resp = MagicMock()
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_resp.status = 200
        with patch("urllib.request.urlopen", return_value=mock_resp):
            result = pub.publish(_paper_signal(), gate)
        self.assertFalse(result.dry_run)
        self.assertTrue(result.published)


# ---------------------------------------------------------------------------
# 5. Telegram routing
# ---------------------------------------------------------------------------

class TestTelegramRouting(unittest.TestCase):

    def test_tier_a_routed_to_vip_channel(self):
        pub = TelegramPublisher(
            bot_token="tok", vip_channel_id="@vip", standard_channel_id="@std",
            vip_tier_threshold="TIER_A", dry_run=True,
        )
        result = pub.publish(_paper_signal(tier="TIER_A"), _gate(publish=True))
        self.assertEqual(result.channel, "@vip")

    def test_tier_s_routed_to_vip_channel(self):
        pub = TelegramPublisher(
            bot_token="tok", vip_channel_id="@vip", standard_channel_id="@std",
            vip_tier_threshold="TIER_A", dry_run=True,
        )
        result = pub.publish(_paper_signal(tier="TIER_S"), _gate(publish=True))
        self.assertEqual(result.channel, "@vip")

    def test_tier_b_routed_to_standard_channel(self):
        pub = TelegramPublisher(
            bot_token="tok", vip_channel_id="@vip", standard_channel_id="@std",
            vip_tier_threshold="TIER_A", dry_run=True,
        )
        result = pub.publish(_paper_signal(tier="TIER_B"), _gate(publish=True))
        self.assertEqual(result.channel, "@std")

    def test_suppressed_gate_not_sent(self):
        pub = TelegramPublisher(
            bot_token="tok", vip_channel_id="@vip", standard_channel_id="@std",
            dry_run=True,
        )
        result = pub.publish(_paper_signal(), _gate(publish=False))
        self.assertFalse(result.published)
        self.assertIsNone(result.channel)

    def test_formatter_vip_contains_tier(self):
        text = SignalFormatter.format_vip(_paper_signal(tier="TIER_A"))
        self.assertIn("TIER", text)
        self.assertIn("HOME", text)
        self.assertIn("1X2", text)

    def test_formatter_standard_contains_edge(self):
        text = SignalFormatter.format_standard(_paper_signal())
        self.assertIn("Edge", text)
        self.assertIn("HOME", text)


# ---------------------------------------------------------------------------
# 6. Health snapshot
# ---------------------------------------------------------------------------

class TestHealthSnapshot(unittest.TestCase):

    def test_snapshot_has_required_fields(self):
        gw = _GatewayStub()
        health = HealthMonitor()
        rt = _runtime(gateway=gw, health=health)
        snap = rt.health_snapshot()
        d = snap.to_dict()
        for key in ("timestamp", "control_state", "risk_index", "provider_health",
                    "completeness_score", "signals_published", "signals_suppressed",
                    "degraded", "iteration_count"):
            self.assertIn(key, d, f"missing key: {key}")

    def test_counters_accumulate_across_iterations(self):
        spy = _PublisherSpy()
        gw = _GatewayStub(publish=True)
        health = HealthMonitor()
        rt = _runtime(signals=[_paper_signal()], gateway=gw, publisher=spy, health=health)
        rt.run_once()
        rt.run_once()
        snap = rt.health_snapshot()
        self.assertEqual(snap.signals_published, 2)

    def test_degraded_flag_reflected_in_snapshot(self):
        cfg = RuntimeConfig(
            telegram=TelegramConfig(dry_run=True),
            scheduler=SchedulerConfig(),
            degraded_failure_threshold=1,
        )
        rt = _runtime(bridge=_BridgeStub(raise_error=True), cfg=cfg)
        rt.run_once()
        snap = rt.health_snapshot()
        self.assertTrue(snap.degraded)


# ---------------------------------------------------------------------------
# 7. Single-instance lock
# ---------------------------------------------------------------------------

class TestSingleInstanceLock(unittest.TestCase):

    def test_acquire_succeeds_first_time(self):
        with tempfile.NamedTemporaryFile(suffix=".lock", delete=False) as f:
            path = f.name
        lock = SingleInstanceLock(path)
        try:
            self.assertTrue(lock.acquire())
        finally:
            lock.release()

    def test_double_acquire_fails(self):
        with tempfile.NamedTemporaryFile(suffix=".lock", delete=False) as f:
            path = f.name
        lock1 = SingleInstanceLock(path)
        lock2 = SingleInstanceLock(path)
        lock1.acquire()
        try:
            self.assertFalse(lock2.acquire())
        finally:
            lock1.release()

    def test_context_manager_releases(self):
        with tempfile.NamedTemporaryFile(suffix=".lock", delete=False) as f:
            path = f.name
        lock = SingleInstanceLock(path)
        with lock:
            pass
        # should be re-acquirable after context exit
        lock2 = SingleInstanceLock(path)
        try:
            self.assertTrue(lock2.acquire())
        finally:
            lock2.release()

    def test_context_manager_raises_if_held(self):
        with tempfile.NamedTemporaryFile(suffix=".lock", delete=False) as f:
            path = f.name
        lock1 = SingleInstanceLock(path)
        lock1.acquire()
        lock2 = SingleInstanceLock(path)
        try:
            with self.assertRaises(RuntimeError):
                with lock2:
                    pass
        finally:
            lock1.release()


# ---------------------------------------------------------------------------
# 8. Config validation
# ---------------------------------------------------------------------------

class TestConfigValidation(unittest.TestCase):

    def test_valid_dry_run_config_passes(self):
        cfg = RuntimeConfig(telegram=TelegramConfig(dry_run=True))
        cfg.validate()  # should not raise

    def test_live_mode_without_channels_fails(self):
        cfg = RuntimeConfig(telegram=TelegramConfig(dry_run=False, vip_channel_id=""))
        with self.assertRaises(ValueError):
            cfg.validate()

    def test_live_mode_with_channels_passes(self):
        cfg = RuntimeConfig(
            telegram=TelegramConfig(
                dry_run=False, vip_channel_id="@vip", standard_channel_id="@std"
            )
        )
        cfg.validate()  # should not raise

    def test_invalid_poll_interval_fails(self):
        cfg = RuntimeConfig(
            scheduler=SchedulerConfig(poll_interval_seconds=-1.0),
            telegram=TelegramConfig(dry_run=True),
        )
        with self.assertRaises(ValueError):
            cfg.validate()

    def test_invalid_tier_threshold_fails(self):
        cfg = RuntimeConfig(
            telegram=TelegramConfig(dry_run=True, vip_tier_threshold="TIER_Z")
        )
        with self.assertRaises(ValueError):
            cfg.validate()


# ---------------------------------------------------------------------------
# 9. Restart / crash recovery
# ---------------------------------------------------------------------------

class TestRestartRecovery(unittest.TestCase):

    def test_gateway_replay_deterministic_after_restart(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name

        plane1 = ControlPlane(db_path=db_path)
        gw1 = ControlGateway(plane1, db_path=db_path + ".gate")
        gw1.gate(_paper_signal(), signal_id="s1")
        r1 = gw1.replay()
        gw1.close(); plane1.close()

        plane2 = ControlPlane(db_path=db_path)
        gw2 = ControlGateway(plane2, db_path=db_path + ".gate")
        r2 = gw2.replay()
        gw2.close(); plane2.close()

        self.assertEqual(r1["n_gated"], r2["n_gated"])

    def test_chain_integrity_verified_on_restart(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name

        plane = ControlPlane(db_path=db_path)
        gw = ControlGateway(plane, db_path=db_path + ".gate")
        gw.gate(_paper_signal(), signal_id="s1")
        gw.gate(_paper_signal(tier="TIER_A"), signal_id="s2")
        self.assertTrue(gw.verify_chain())
        gw.close(); plane.close()


# ---------------------------------------------------------------------------
# 10. Additivity — M11 hash unchanged
# ---------------------------------------------------------------------------

class TestAdditivity(unittest.TestCase):

    def test_m11_acceptance_hash_unchanged(self):
        import tests.test_m11_acceptance as m
        self.assertEqual(
            m.run_hash(m.baseline_providers()),
            m.TestM11Acceptance.BASELINE_HASH,
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
