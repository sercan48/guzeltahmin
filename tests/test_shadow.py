"""M7 shadow run & simulation invariants. Deterministic, no network, no execution."""

import unittest

from src.market.shadow import (
    ShadowRunner, DriftInjection, DriftType, TimelineConfig, generate_timeline,
    SilentFailureDetector, SystemHealthKernel, ShadowPaperDivergence, WindowStat,
    peak_day_report, partial_outage_report, odds_burst_report, default_sim_model,
)
from datetime import datetime, timezone

KO = datetime(2026, 4, 4, 15, 0, tzinfo=timezone.utc)


class TestTimeline(unittest.TestCase):
    def test_timeline_deterministic(self):
        a = generate_timeline("m", KO, seed=7, config=TimelineConfig(noise_std=0.02))
        b = generate_timeline("m", KO, seed=7, config=TimelineConfig(noise_std=0.02))
        self.assertEqual([(t.type, t.idempotency_key, t.payload) for t in a],
                         [(t.type, t.idempotency_key, t.payload) for t in b])

    def test_seed_changes_noise(self):
        a = generate_timeline("m", KO, seed=1, config=TimelineConfig(noise_std=0.03))
        b = generate_timeline("m", KO, seed=2, config=TimelineConfig(noise_std=0.03))
        self.assertNotEqual([t.payload for t in a], [t.payload for t in b])

    def test_api_delay_drops_provider(self):
        drifts = [DriftInjection(DriftType.API_DELAY, at_tick=2, provider="pinnacle")]
        trigs = generate_timeline("m", KO, drifts=drifts)
        tick2 = next(t for t in trigs if t.idempotency_key.endswith("tick2"))
        providers = {s["provider"] for s in tick2.payload["snapshots"]}
        self.assertNotIn("pinnacle", providers)


class TestShadowRun(unittest.TestCase):
    def test_run_produces_signals_and_windows(self):
        run = ShadowRunner().run(seed=0)
        self.assertGreater(run.signal_count, 0)
        self.assertTrue(run.windows)
        self.assertEqual(sum(w.n_signals for w in run.windows), run.signal_count)

    def test_replay_determinism(self):
        r = ShadowRunner()
        a = r.run(seed=5).to_dict()
        b = r.run(seed=5).to_dict()
        self.assertEqual(a, b)

    def test_shadow_vs_paper_report_deterministic(self):
        r = ShadowRunner()
        d = [DriftInjection(DriftType.SHARP_MOVE, at_tick=4, magnitude=0.08)]
        a = r.shadow_vs_paper(seed=3, drifts=d, noise_std=0.01).to_dict()
        b = r.shadow_vs_paper(seed=3, drifts=d, noise_std=0.01).to_dict()
        self.assertEqual(a, b)

    def test_health_score_bounded(self):
        rep = ShadowRunner().shadow_vs_paper(seed=2, noise_std=0.01)
        self.assertGreaterEqual(rep.health.composite, 0.0)
        self.assertLessEqual(rep.health.composite, 100.0)
        self.assertGreaterEqual(rep.stability_score, 0.0)
        self.assertLessEqual(rep.stability_score, 100.0)
        for v in rep.health.subscores.values():
            self.assertGreaterEqual(v, 0.0)
            self.assertLessEqual(v, 1.0)

    def test_clean_run_has_no_silent_failures(self):
        rep = ShadowRunner().shadow_vs_paper(seed=1, noise_std=0.005)
        self.assertEqual(rep.silent_failures, [])

    def test_drift_heatmap_records_injection(self):
        d = [DriftInjection(DriftType.SHARP_MOVE, at_tick=4, magnitude=0.08)]
        rep = ShadowRunner().shadow_vs_paper(seed=3, drifts=d, noise_std=0.01)
        self.assertEqual(rep.drift_heatmap["SHARP_MOVE"][4], 0.08)
        self.assertIn("OBSERVED_IMPACT", rep.drift_heatmap)


class TestSilentFailure(unittest.TestCase):
    def test_no_signal_bug_detected(self):
        # windows with traffic but zero signals anywhere -> no_signal_bug + dead_zone
        windows = [WindowStat(i, n_triggers=2) for i in range(4)]
        flags = SilentFailureDetector().scan(windows)
        types = {f.type for f in flags}
        self.assertIn("no_signal_bug", types)
        self.assertIn("orchestrator_dead_zone", types)

    def test_clv_collapse_detected(self):
        windows = [
            WindowStat(0, n_triggers=2, n_signals=1, edge_values=[0.05], truth_conf=[0.9]),
            WindowStat(1, n_triggers=2, n_signals=1, edge_values=[0.04], truth_conf=[0.6]),
            WindowStat(2, n_triggers=2, n_signals=1, edge_values=[0.03], truth_conf=[0.2]),
        ]
        flags = SilentFailureDetector().scan(windows)
        self.assertIn("silent_clv_collapse", {f.type for f in flags})

    def test_edge_stagnation_detected(self):
        windows = [WindowStat(i, n_triggers=2, n_signals=1, edge_values=[0.05],
                              truth_conf=[0.9], tiers=["TIER_B"]) for i in range(4)]
        flags = SilentFailureDetector().scan(windows)
        self.assertIn("edge_stagnation", {f.type for f in flags})

    def test_healthy_run_no_flags(self):
        windows = [WindowStat(i, n_triggers=2, n_signals=1, edge_values=[0.05 + i * 0.01],
                              truth_conf=[0.9], tiers=["TIER_A" if i % 2 else "TIER_B"])
                   for i in range(4)]
        self.assertEqual(SilentFailureDetector().scan(windows), [])


class TestDivergenceAndHealth(unittest.TestCase):
    def test_zero_divergence_for_identical(self):
        w = [WindowStat(i, n_triggers=1, n_signals=1, edge_values=[0.05]) for i in range(3)]
        d = ShadowPaperDivergence().compute(w, w)
        self.assertAlmostEqual(d.spg, 0.0)
        self.assertEqual(d.cr, 1.0)

    def test_divergence_grows_with_gap(self):
        shadow = [WindowStat(i, n_triggers=1, n_signals=1, edge_values=[0.10]) for i in range(3)]
        paper = [WindowStat(i, n_triggers=1, n_signals=1, edge_values=[0.05]) for i in range(3)]
        d = ShadowPaperDivergence(epsilon=0.02).compute(shadow, paper)
        self.assertGreater(d.spg, 0.02)
        self.assertEqual(d.cr, 0.0)

    def test_entropy_zero_for_single_tier(self):
        windows = [WindowStat(0, n_triggers=2, n_signals=2, edge_values=[0.05, 0.05],
                              tiers=["TIER_B", "TIER_B"])]
        score = SystemHealthKernel().score(windows)
        self.assertEqual(score.subscores["edge_entropy"], 0.0)


class TestStressScenarios(unittest.TestCase):
    def test_peak_day(self):
        rep = peak_day_report(ShadowRunner(), n_matches=8, seed=1)
        self.assertGreater(rep.run.triggers, 8)        # many concurrent matches
        self.assertGreaterEqual(rep.stability_score, 0.0)

    def test_partial_outage_runs(self):
        rep = partial_outage_report(ShadowRunner())
        self.assertIsNotNone(rep.health)
        self.assertGreaterEqual(rep.stability_score, 0.0)

    def test_odds_burst_runs(self):
        rep = odds_burst_report(ShadowRunner())
        self.assertGreater(rep.run.signal_count, 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
