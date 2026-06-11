"""M9.2 telemetry integration & signal gating invariants. No network, no betting."""

import os
import tempfile
import unittest

from src.market.orchestration import PaperSignal
from src.market.control import (
    ControlPlane, ControlGateway, TelemetryAdapter, ControlMetrics, SystemState,
    ControlDecision, ReasonCode,
)
from src.market.shadow import SystemHealthKernel, ShadowPaperDivergence, WindowStat
from src.market.settlement import SettlementMathEngine


def paper(tc=0.85, match="m", sel="HOME"):
    return PaperSignal(match, "1X2", sel, edge_score=0.07, tier="TIER_A",
                       confidence=0.82, truth_confidence=tc, timestamp="2026-02-01T17:00:00+00:00")


def good_metrics():
    return ControlMetrics(health_v2=95, stability=90, cr=0.9, spg=0.01, clv_realized=0.03,
                          beat_rate=0.55, roi_realized=0.05, max_drawdown=0.05,
                          settlement_confidence=0.9, data_coverage=0.9, truth_lag_norm=0.2)


def gw(state=SystemState.LIVE):
    return ControlGateway(ControlPlane(":memory:", initial_state=state), ":memory:")


class TestTelemetryAdapter(unittest.TestCase):
    def test_build_from_m7_m8(self):
        windows = [WindowStat(i, n_triggers=2, n_signals=1, edge_values=[0.05 + 0.01 * i],
                              tiers=["TIER_A" if i % 2 else "TIER_B"], truth_conf=[0.9])
                   for i in range(4)]
        health = SystemHealthKernel().score(windows)
        div = ShadowPaperDivergence().compute(windows, windows)   # zero gap
        eng = SettlementMathEngine(":memory:")
        eng.finalize("b1", "m", "1X2", "HOME", 2.0, result="WON", y=1, o_close=1.9,
                     p_close=0.5, p_model=0.58, outcome_conf=1.0, close_conf=0.9, truth_conf=0.85)
        summary = eng.replay()
        m = TelemetryAdapter.build(m7_health=health, m7_divergence=div, m8_metrics=summary,
                                   data_coverage=0.9, truth_lag_norm=0.1)
        self.assertEqual(m.health_v2, health.composite)
        self.assertEqual(m.cr, 1.0)
        self.assertAlmostEqual(m.spg, 0.0)
        self.assertEqual(m.settlement_confidence, summary.mean_settlement_confidence)
        eng.close()

    def test_silent_failure_flag(self):
        m = TelemetryAdapter.build(m7_silent_failures=["some_flag"])
        self.assertTrue(m.silent_failure_critical)


class TestGating(unittest.TestCase):
    def test_allow_publishes(self):
        g = gw(SystemState.LIVE)
        g.evaluate(good_metrics())
        r = g.gate(paper())
        self.assertEqual(r.decision, ControlDecision.ALLOW.value)
        self.assertTrue(r.publish)
        self.assertIn(ReasonCode.ALLOWED.value, r.reason_codes)
        g.close()

    def test_shadow_silent_blocks_publish_keeps_measurement(self):
        g = gw(SystemState.SHADOW)
        m = good_metrics(); m.cr = 0.7      # fails PAPER promote -> stays SHADOW
        g.evaluate(m)
        r = g.gate(paper())
        self.assertEqual(r.decision, ControlDecision.DEGRADE.value)
        self.assertFalse(r.publish)         # publication blocked
        self.assertTrue(r.recorded)         # measurement continues
        self.assertIn(ReasonCode.SILENT_MODE_SHADOW.value, r.reason_codes)
        g.close()

    def test_locked_suppresses(self):
        plane = ControlPlane(":memory:", initial_state=SystemState.LIVE)
        g = ControlGateway(plane, ":memory:")
        g.evaluate(ControlMetrics(manual_kill=True))   # -> LOCKED, HALT
        r = g.gate(paper())
        self.assertFalse(r.publish)
        self.assertEqual(r.decision, ControlDecision.HALT.value)
        self.assertIn(ReasonCode.KILL_HALT.value, r.reason_codes)
        g.close()

    def test_off_suppresses(self):
        g = gw(SystemState.OFF)
        # failing-gate metrics keep OFF, decision SUPPRESS
        m = good_metrics(); m.data_coverage = 0.5
        g.evaluate(m)
        r = g.gate(paper())
        self.assertFalse(r.publish)
        self.assertIn(ReasonCode.STATE_OFF.value, r.reason_codes)
        g.close()

    def test_per_signal_low_truth_confidence_suppressed(self):
        g = gw(SystemState.LIVE)
        g.evaluate(good_metrics())               # plane ALLOW
        r = g.gate(paper(tc=0.1))                # but signal truth conf too low
        self.assertFalse(r.publish)
        self.assertIn(ReasonCode.LOW_TRUTH_CONFIDENCE.value, r.reason_codes)
        g.close()

    def test_risk_throttle_degrades(self):
        g = gw(SystemState.PAPER)
        m = ControlMetrics(health_v2=58, stability=60, cr=0.7, spg=0.08, clv_realized=0.0,
                           beat_rate=0.5, roi_realized=0.0, max_drawdown=0.2,
                           settlement_confidence=0.5, data_coverage=0.8, truth_lag_norm=0.9)
        g.evaluate(m)
        r = g.gate(paper())
        self.assertFalse(r.publish)
        self.assertIn(ReasonCode.RISK_THROTTLE.value, r.reason_codes)
        g.close()


class TestSuppressionLedgerAndReplay(unittest.TestCase):
    def _run(self, g):
        g.evaluate(good_metrics())
        g.gate_batch([paper(sel="HOME"), paper(sel="DRAW", tc=0.1), paper(sel="AWAY")])

    def test_ledger_and_counts(self):
        g = gw(SystemState.LIVE)
        self._run(g)
        rep = g.replay()
        self.assertEqual(rep["n_gated"], 3)
        self.assertEqual(rep["n_published"], 2)       # HOME, AWAY allowed; DRAW low-conf
        self.assertEqual(rep["n_suppressed"], 1)
        self.assertTrue(rep["chain_valid"])
        g.close()

    def test_chain_tamper(self):
        g = gw(SystemState.LIVE)
        self._run(g)
        g.conn.execute("UPDATE suppression_ledger SET publish=1 WHERE seq=2")
        g.conn.commit()
        self.assertFalse(g.verify_chain())
        g.close()

    def test_replay_deterministic(self):
        a = gw(SystemState.LIVE); self._run(a)
        b = gw(SystemState.LIVE); self._run(b)
        self.assertEqual(a.replay(), b.replay())
        a.close(); b.close()

    def test_gating_results_replay_identically(self):
        sigs = [paper(sel="HOME"), paper(sel="DRAW", tc=0.1), paper(sel="AWAY")]
        a = gw(SystemState.LIVE); a.evaluate(good_metrics())
        b = gw(SystemState.LIVE); b.evaluate(good_metrics())
        ra = [r.to_dict() for r in a.gate_batch(sigs)]
        rb = [r.to_dict() for r in b.gate_batch(sigs)]
        self.assertEqual(ra, rb)
        a.close(); b.close()

    def test_replay_safe_reopen(self):
        path = os.path.join(tempfile.mkdtemp(), "supp.db")
        try:
            g = ControlGateway(ControlPlane(":memory:", initial_state=SystemState.LIVE), path)
            self._run(g)
            r1 = g.replay()
            g.close()
            g2 = ControlGateway(ControlPlane(":memory:", initial_state=SystemState.LIVE), path)
            self.assertEqual(g2.replay(), r1)
            g2.close()
        finally:
            if os.path.exists(path):
                os.remove(path)


class TestMonitoring(unittest.TestCase):
    def test_monitor_fields(self):
        g = gw(SystemState.LIVE)
        g.evaluate(good_metrics())
        g.gate(paper())
        mon = g.monitor()
        for k in ("active_state", "last_decision", "risk_index", "active_suppressions",
                  "gate_failures", "telemetry_snapshot"):
            self.assertIn(k, mon)
        self.assertEqual(mon["active_state"], "LIVE")
        g.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
