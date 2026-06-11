"""M9.1 production control plane core invariants. No network, no betting."""

import os
import tempfile
import unittest

from src.market.control import (
    ControlPlane, ControlMetrics, ControlConfig, SystemState, ControlDecision,
    kill_factors, risk_index, evaluate_gate,
)

CFG = ControlConfig()


def good():
    return ControlMetrics(health_v2=95, stability=90, cr=0.9, spg=0.01, clv_realized=0.03,
                          beat_rate=0.55, roi_realized=0.05, max_drawdown=0.05,
                          settlement_confidence=0.9, data_coverage=0.9, truth_lag_norm=0.2)


class TestStateMachineAndPromotion(unittest.TestCase):
    def test_promotes_one_rung_per_eval_to_live(self):
        cp = ControlPlane(":memory:", initial_state=SystemState.OFF)
        states = []
        for _ in range(5):
            states.append(cp.evaluate(good()).state)
        self.assertEqual(states, ["SHADOW", "PAPER", "MICRO", "LIVE", "LIVE"])
        cp.close()

    def test_gate_blocks_promotion(self):
        cp = ControlPlane(":memory:", initial_state=SystemState.OFF)
        m = good(); m.data_coverage = 0.5    # fails SHADOW cov_min
        out = cp.evaluate(m)
        self.assertEqual(out.state, "OFF")   # no promotion
        self.assertFalse(out.promotion_gate_passed)
        cp.close()

    def test_demotion_on_exit_breach(self):
        cp = ControlPlane(":memory:", initial_state=SystemState.LIVE)
        m = good(); m.health_v2 = 80         # below LIVE exit floor 82 (but >kill floor)
        out = cp.evaluate(m)
        self.assertEqual(out.state, "MICRO")
        self.assertEqual(out.transition[2], "DEMOTE")
        cp.close()

    def test_off_suppresses_when_gate_fails(self):
        cp = ControlPlane(":memory:", initial_state=SystemState.OFF)
        m = good(); m.data_coverage = 0.5
        out = cp.evaluate(m)
        self.assertEqual(out.decision, ControlDecision.SUPPRESS.value)
        cp.close()


class TestKillSwitch(unittest.TestCase):
    def test_kill_dominates_promotion(self):
        cp = ControlPlane(":memory:", initial_state=SystemState.MICRO)
        m = good(); m.manual_kill = True     # would otherwise promote to LIVE
        out = cp.evaluate(m)
        self.assertEqual(out.state, "LOCKED")
        self.assertEqual(out.decision, ControlDecision.HALT.value)
        self.assertTrue(out.kill)
        cp.close()

    def test_hard_kill_health(self):
        cp = ControlPlane(":memory:", initial_state=SystemState.LIVE)
        m = good(); m.health_v2 = 10         # below kill floor
        out = cp.evaluate(m)
        self.assertEqual(out.state, "LOCKED")
        self.assertTrue(out.kill_factors["k_health"])
        cp.close()

    def test_locked_cannot_auto_promote(self):
        cp = ControlPlane(":memory:", initial_state=SystemState.LIVE)
        cp.evaluate(ControlMetrics(manual_kill=True))   # -> LOCKED
        self.assertEqual(cp.state, SystemState.LOCKED)
        out = cp.evaluate(good())                        # perfect metrics, no kill
        self.assertEqual(out.state, "LOCKED")            # still locked
        self.assertEqual(out.decision, ControlDecision.SUPPRESS.value)
        cp.close()

    def test_manual_reset_only_from_locked_without_kill(self):
        cp = ControlPlane(":memory:", initial_state=SystemState.PAPER)
        self.assertFalse(cp.manual_reset(good()))        # not locked
        cp.evaluate(ControlMetrics(manual_kill=True))    # -> LOCKED
        self.assertFalse(cp.manual_reset(ControlMetrics(manual_kill=True)))  # kill still active
        self.assertTrue(cp.manual_reset(good()))         # clean -> SHADOW
        self.assertEqual(cp.state, SystemState.SHADOW)
        cp.close()

    def test_force_kill(self):
        cp = ControlPlane(":memory:", initial_state=SystemState.LIVE)
        out = cp.force_kill("ops")
        self.assertEqual(out.state, "LOCKED")
        self.assertEqual(out.decision, ControlDecision.HALT.value)
        cp.close()


class TestRiskIndex(unittest.TestCase):
    def test_bounded(self):
        self.assertGreaterEqual(risk_index(good(), CFG, False), 0.0)
        self.assertLessEqual(risk_index(good(), CFG, False), 100.0)
        self.assertEqual(risk_index(good(), CFG, True), 100.0)   # kill -> max

    def test_monotonic_in_health(self):
        better = good()
        worse = good(); worse.health_v2 = 40
        self.assertGreater(risk_index(worse, CFG, False), risk_index(better, CFG, False))

    def test_monotonic_in_drawdown(self):
        better = good()
        worse = good(); worse.max_drawdown = 0.20
        self.assertGreater(risk_index(worse, CFG, False), risk_index(better, CFG, False))

    def test_high_risk_degrades_in_tradeable_state(self):
        cp = ControlPlane(":memory:", initial_state=SystemState.PAPER)
        m = ControlMetrics(health_v2=58, stability=60, cr=0.7, spg=0.08, clv_realized=0.0,
                           beat_rate=0.5, roi_realized=0.0, max_drawdown=0.2,
                           settlement_confidence=0.5, data_coverage=0.8, truth_lag_norm=0.9)
        out = cp.evaluate(m)
        self.assertGreaterEqual(out.risk_index, CFG.throttle_risk)
        self.assertEqual(out.state, "PAPER")             # exit floor (health>=55) holds
        self.assertEqual(out.decision, ControlDecision.DEGRADE.value)
        cp.close()

    def test_shadow_is_silent(self):
        cp = ControlPlane(":memory:", initial_state=SystemState.SHADOW)
        m = good(); m.health_v2 = 60   # passes shadow exit, fails PAPER promote (cr ok but...)
        m.cr = 0.5                      # fails PAPER cr_min 0.8 -> no promote
        out = cp.evaluate(m)
        self.assertEqual(out.state, "SHADOW")
        self.assertEqual(out.decision, ControlDecision.DEGRADE.value)
        cp.close()


class TestGateEvaluation(unittest.TestCase):
    def test_gate_detail(self):
        passed, detail = evaluate_gate(CFG.promote["LIVE"], good())
        self.assertTrue(passed)
        self.assertTrue(all(detail.values()))

    def test_gate_fails_on_one(self):
        m = good(); m.roi_realized = -0.1
        passed, detail = evaluate_gate(CFG.promote["MICRO"], m)
        self.assertFalse(passed)
        self.assertFalse(detail["roi_min"])

    def test_kill_factors(self):
        kf = kill_factors(ControlMetrics(spg=0.2, cr=0.9, health_v2=90,
                                         settlement_confidence=0.9), CFG)
        self.assertTrue(kf["k_drift"])      # spg 0.2 > 0.10
        self.assertFalse(kf["k_health"])


class TestAuditAndReplay(unittest.TestCase):
    def _run(self, cp):
        for _ in range(4):
            cp.evaluate(good())          # promote OFF->...->LIVE
        cp.evaluate(ControlMetrics(manual_kill=True))   # -> LOCKED

    def test_chain_valid_and_tamper(self):
        cp = ControlPlane(":memory:")
        self._run(cp)
        self.assertTrue(cp.verify_chain())
        cp.conn.execute("UPDATE control_audit SET to_state='LIVE' WHERE seq=1")
        cp.conn.commit()
        self.assertFalse(cp.verify_chain())
        cp.close()

    def test_replay_final_state(self):
        cp = ControlPlane(":memory:")
        self._run(cp)
        rep = cp.replay()
        self.assertEqual(rep["final_state"], "LOCKED")
        self.assertTrue(rep["chain_valid"])
        cp.close()

    def test_decisions_replay_identically(self):
        seq = [good(), good(), good(), good(), good()]
        a = ControlPlane(":memory:")
        b = ControlPlane(":memory:")
        out_a = [a.evaluate(m).to_dict() for m in seq]
        out_b = [b.evaluate(m).to_dict() for m in seq]
        self.assertEqual(out_a, out_b)
        self.assertEqual(a.replay()["final_state"], b.replay()["final_state"])
        a.close(); b.close()

    def test_replay_safe_reopen(self):
        path = os.path.join(tempfile.mkdtemp(), "ctrl.db")
        try:
            cp = ControlPlane(path)
            self._run(cp)
            r1 = cp.replay()
            cp.close()
            cp2 = ControlPlane(path)
            self.assertEqual(cp2.replay(), r1)
            cp2.close()
        finally:
            if os.path.exists(path):
                os.remove(path)


class TestMonitoring(unittest.TestCase):
    def test_status_fields(self):
        cp = ControlPlane(":memory:", initial_state=SystemState.OFF)
        cp.evaluate(good())   # -> SHADOW
        st = cp.status()
        for k in ("current_state", "risk_index", "kill_factors_active",
                  "next_promotion_target", "active_gates", "active_suppressions",
                  "last_transition"):
            self.assertIn(k, st)
        self.assertEqual(st["current_state"], "SHADOW")
        self.assertEqual(st["next_promotion_target"], "PAPER")
        cp.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
