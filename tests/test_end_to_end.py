"""End-to-end pipeline proof — all stages wired together, no module changes.

  Scheduler -> Provider -> Truth -> Measurement -> Edge -> Truth Adjust
            -> Signal -> Control Gate -> Settlement -> Performance -> Final Grade

Pure-stdlib, deterministic, network-free. This is an integration harness in the
test layer; it imports the real modules and connects them exactly as production
would, asserting each link produces a coherent result.
"""

import unittest
from datetime import datetime, timedelta, timezone

# Stage 1-2
from src.market.scheduler import SnapshotScheduler, ManualClock
from src.market.activation import MockOddsProvider, ProviderOutcome, IngestionBridge
# Stage 3-6
from src.market.truth import TruthStore, TruthAdapter, MeasurementMode, TruthEdgeAdjuster
from src.market.schema import MatchContext, Horizon
from src.market.edge import EdgeDetectionKernel, SegmentMeta
# Stage 7-8
from src.market.orchestration import PaperSignal
from src.market.control import ControlPlane, ControlGateway, ControlMetrics, SystemState
# Stage 9-11
from src.market.settlement import (
    SettlementLedger, ClosureLedger, SettlementMathEngine, MatchOutcome,
    PerformanceAggregator, SignalGradingEngine, SignalInput, HitMiss,
)

KO = datetime(2026, 4, 1, 18, 0, tzinfo=timezone.utc)
MATCH, MARKET, SEL = "epl_ars_che", "1X2", "HOME"

# HOME shortens across the timeline (sharp backing); vig-inclusive odds.
_PATH = {
    "T-72h": {"HOME": 2.30, "DRAW": 3.30, "AWAY": 3.10},
    "T-48h": {"HOME": 2.20, "DRAW": 3.35, "AWAY": 3.25},
    "T-24h": {"HOME": 2.10, "DRAW": 3.40, "AWAY": 3.45},
    "T-12h": {"HOME": 2.05, "DRAW": 3.45, "AWAY": 3.55},
    "T-6h": {"HOME": 2.00, "DRAW": 3.50, "AWAY": 3.65},
    "T-1h": {"HOME": 1.95, "DRAW": 3.55, "AWAY": 3.75},
    "CLOSE": {"HOME": 1.90, "DRAW": 3.60, "AWAY": 3.90},
}
MODEL_PROBS = {(MATCH, MARKET, "HOME"): 0.58, (MATCH, MARKET, "DRAW"): 0.24,
               (MATCH, MARKET, "AWAY"): 0.18}


def _providers():
    fixture = {MATCH: _PATH}
    return [
        MockOddsProvider("pinnacle", "SHARP", fixture,
                         outcomes={MATCH: ProviderOutcome("COMPLETED", 2, 1)}),  # HOME win
        MockOddsProvider("bet365", "SEMI_SHARP", fixture),
    ]


class TestEndToEndPipeline(unittest.TestCase):
    def test_full_chain(self):
        # ---- 1. SCHEDULER ------------------------------------------------
        clock = ManualClock(KO - timedelta(hours=100))
        sched = SnapshotScheduler(clock, ":memory:")
        sched.schedule_match(MATCH, KO)

        # ---- 2. PROVIDER (+ ingestion bridge) ----------------------------
        truth = TruthStore(":memory:")
        settle = SettlementLedger(":memory:")
        bridge = IngestionBridge(sched, truth, _providers(), settlement_ledger=settle,
                                 db_path=":memory:")
        clock.set(KO)                       # all 7 ticks due
        results = bridge.process_due()
        self.assertEqual(len(results), 7)
        self.assertTrue(all(r.status == "SUCCESS" for r in results))

        # ---- 3. TRUTH ----------------------------------------------------
        close_truth = truth.get_closing_truth(MATCH, MARKET, SEL)
        self.assertIsNotNone(close_truth)
        self.assertIn("pinnacle", close_truth.contributing_providers)

        # ---- 4. MEASUREMENT (via Truth-rule adapter) ---------------------
        contexts = {MATCH: MatchContext(MATCH, KO)}
        adapter = TruthAdapter(truth, MeasurementMode.TRUTH_ONLY)
        measurement, truth_meta, _ = adapter.run_measurement(contexts, consensus_horizon=Horizon.H1)
        drift = measurement.drift[f"{MATCH}|{MARKET}|HOME@consensus"]
        self.assertEqual(drift.direction, "SHORTENING")     # HOME was backed

        # ---- 5. EDGE -----------------------------------------------------
        seg = {MATCH: SegmentMeta(calibration_quality=0.85, clv_alignment=0.65)}
        edges = EdgeDetectionKernel().run(measurement, MODEL_PROBS, seg)
        edge = edges[(MATCH, MARKET, SEL)]
        self.assertGreater(edge.metrics.sharp_adjusted_edge, 0)

        # ---- 6. TRUTH ADJUST (discount-only) -----------------------------
        adjusted = TruthEdgeAdjuster().adjust_batch(edges, truth_meta)[(MATCH, MARKET, SEL)]
        self.assertLessEqual(adjusted.edge_after_truth, edge.metrics.sharp_adjusted_edge + 1e-12)
        self.assertNotEqual(adjusted.tier_after, "REJECT")

        # ---- 7. SIGNAL ---------------------------------------------------
        entry = truth.get_truth(MATCH, MARKET, SEL, "T-1h").o_truth   # pre-close entry price
        signal = PaperSignal(MATCH, MARKET, SEL, edge_score=adjusted.edge_after_truth,
                             tier=adjusted.tier_after,
                             confidence=edge.confidence.edge_confidence_score,
                             truth_confidence=truth_meta[(MATCH, MARKET, SEL)].confidence,
                             timestamp=(KO - timedelta(hours=1)).isoformat())

        # ---- 8. CONTROL GATE ---------------------------------------------
        plane = ControlPlane(":memory:", initial_state=SystemState.LIVE)
        gateway = ControlGateway(plane, ":memory:")
        gateway.evaluate(ControlMetrics(health_v2=95, stability=90, cr=0.9, spg=0.01,
                                        clv_realized=0.03, beat_rate=0.55, roi_realized=0.05,
                                        max_drawdown=0.05, settlement_confidence=0.9,
                                        data_coverage=1.0, truth_lag_norm=0.1))
        gate = gateway.gate(signal, signal_id="sig-1")
        self.assertTrue(gate.publish)                       # LIVE + ALLOW -> publishable
        self.assertTrue(gate.recorded)                      # measurement pipeline continues

        # ---- 9. SETTLEMENT (outcome + locked close + math) ---------------
        ClosureLedger(":memory:")  # (closure used below from a persistent instance)
        closure = ClosureLedger(":memory:")
        closure.lock(truth, MATCH, MARKET, KO)              # immutable close
        bridge.ingest_outcome(MATCH)                        # provider outcome -> M8.1
        math = SettlementMathEngine(":memory:")
        metric = math.finalize_from_ledgers(settle, closure, bet_id="sig-1", match_id=MATCH,
                                            market=MARKET, selection=SEL, entry_odds=entry,
                                            p_model=0.58, truth_conf=signal.truth_confidence)
        self.assertEqual(metric.result, "WON")
        self.assertAlmostEqual(metric.realized_roi, entry - 1.0, places=6)
        self.assertIsNotNone(metric.realized_clv)
        self.assertAlmostEqual(metric.residual, 0.0, places=9)   # error decomposition balances

        # ---- 10. PERFORMANCE ---------------------------------------------
        perf = PerformanceAggregator(":memory:")
        perf.ingest_from_metric(metric, league="EPL", regime="EFF_STABLE",
                                tier=adjusted.tier_after, source_class="SHARP")
        gsum = perf.global_summary()
        self.assertEqual(gsum.n, 1)
        self.assertAlmostEqual(gsum.roi_total, entry - 1.0, places=6)

        # ---- 11. FINAL GRADE ---------------------------------------------
        grader = SignalGradingEngine(":memory:")
        grader.register(SignalInput.from_paper(signal, signal_id="sig-1", entry_odds=entry,
                                               predicted_clv=adjusted.edge_after_truth,
                                               p_model=0.58, league="EPL", regime="EFF_STABLE",
                                               source_class="SHARP"))
        graded = grader.grade("sig-1", settle, closure, math)
        self.assertEqual(graded.hit_miss, HitMiss.HIT.value)
        self.assertAlmostEqual(graded.realized_roi, entry - 1.0, places=6)
        portfolio = grader.portfolio_summary()
        self.assertEqual(portfolio.n_hit, 1)
        self.assertAlmostEqual(portfolio.hit_rate, 1.0, places=6)

        # close handles
        for h in (sched, truth, settle, bridge, closure, math, perf, grader,
                  plane, gateway):
            h.close()

    def test_reject_signal_not_published(self):
        # a no-edge signal (model == market) should not produce a backable tier
        clock = ManualClock(KO - timedelta(hours=100))
        sched = SnapshotScheduler(clock, ":memory:")
        sched.schedule_match(MATCH, KO)
        truth = TruthStore(":memory:")
        bridge = IngestionBridge(sched, truth, _providers(), db_path=":memory:")
        clock.set(KO); bridge.process_due()
        contexts = {MATCH: MatchContext(MATCH, KO)}
        measurement, meta, _ = TruthAdapter(truth).run_measurement(contexts)
        # model == market -> ~zero edge
        market_home = measurement.efficiency[f"{MATCH}|{MARKET}"].consensus_prob["HOME"]
        flat = {(MATCH, MARKET, s): measurement.efficiency[f"{MATCH}|{MARKET}"].consensus_prob[s]
                for s in ("HOME", "DRAW", "AWAY")}
        edges = EdgeDetectionKernel().run(measurement, flat)
        adj = TruthEdgeAdjuster().adjust_batch(edges, meta)[(MATCH, MARKET, SEL)]
        self.assertEqual(adj.tier_after, "REJECT")
        sched.close(); truth.close(); bridge.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
