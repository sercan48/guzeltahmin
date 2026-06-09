"""M7 — Shadow Runner: drives the existing M5 orchestrator over simulated
timelines, collects per-window statistics, and produces the shadow outputs
(Health Report, Drift Heatmap, Silent Failure Log, System Stability Score).

Simulation + orchestration only. It does NOT change M1-M6, contains no
prediction logic (model probabilities are an injected simulation stub), and
performs no paper/live execution.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Callable, Dict, List, Optional

from ..truth import TruthStore
from ..orchestration import (
    PipelineOrchestrator, LifecycleService, OrchestratorConfig, TriggerType,
)
from .timeline import TimelineConfig, DriftInjection, generate_timeline
from .monitors import (
    WindowStat, SilentFailureDetector, ShadowPaperDivergence, SystemHealthKernel,
    HealthScore, DivergenceResult, Flag,
)

ModelProbProvider = Callable[[str, str], Dict[str, float]]

KO = datetime(2026, 4, 4, 15, 0, tzinfo=timezone.utc)   # an "EPL Saturday" kickoff


def default_sim_model(match_id: str, market: str) -> Dict[str, float]:
    """SIMULATION-ONLY synthetic model probabilities (no prediction logic).

    A constant HOME-favouring view; as the simulated market shortens HOME the
    edge naturally varies across ticks, exercising the monitors.
    """
    return {"HOME": 0.58, "DRAW": 0.24, "AWAY": 0.18}


@dataclass
class RunResult:
    windows: List[WindowStat]
    metrics: dict
    signal_count: int
    triggers: int

    def to_dict(self) -> dict:
        # latency is wall-clock (non-deterministic); excluded so the report is
        # reproducible for replay determinism. Latency is still tracked live.
        metrics = {k: v for k, v in self.metrics.items()
                   if not k.startswith("execution_latency")}
        return {
            "metrics": metrics, "signal_count": self.signal_count,
            "triggers": self.triggers,
            "windows": [
                {"index": w.index, "n_triggers": w.n_triggers, "n_signals": w.n_signals,
                 "n_blocked": w.n_blocked, "edge_mean": round(w.edge_mean, 4),
                 "truth_conf_mean": round(w.truth_conf_mean, 4)}
                for w in self.windows
            ],
        }


@dataclass
class ShadowReport:
    stability_score: float                  # 0..100
    health: HealthScore
    silent_failures: List[Flag]
    drift_heatmap: Dict[str, List[float]]   # row label -> per-tick intensities
    divergence: Optional[DivergenceResult]
    run: RunResult

    def to_dict(self) -> dict:
        return {
            "stability_score": self.stability_score,
            "health": self.health.to_dict(),
            "silent_failures": [f.to_dict() for f in self.silent_failures],
            "drift_heatmap": self.drift_heatmap,
            "divergence": self.divergence.to_dict() if self.divergence else None,
            "run": self.run.to_dict(),
        }


class ShadowRunner:
    def __init__(self, model_provider: ModelProbProvider = default_sim_model,
                 orch_config: Optional[OrchestratorConfig] = None) -> None:
        self.model_provider = model_provider
        self.orch_config = orch_config or OrchestratorConfig(active_min_tier="TIER_C")
        self.detector = SilentFailureDetector()
        self.kernel = SystemHealthKernel()

    # -- one shadow run ----------------------------------------------------
    def run(self, seed: int = 0, drifts: Optional[List[DriftInjection]] = None,
            n_matches: int = 1, config: TimelineConfig = TimelineConfig(),
            kickoff: datetime = KO) -> RunResult:
        store = TruthStore(":memory:")
        orch = PipelineOrchestrator(store, LifecycleService(), self.model_provider,
                                    config=self.orch_config)
        windows: Dict[int, WindowStat] = {}

        for m in range(n_matches):
            match_id = f"shadow_m{m}"
            triggers = generate_timeline(match_id, kickoff, seed + m, drifts, config)
            for trig in triggers:
                result = orch.handle_trigger(trig)
                if trig.type != TriggerType.ODDS_UPDATED:
                    continue
                idx = int(trig.idempotency_key.rsplit("tick", 1)[1])
                w = windows.setdefault(idx, WindowStat(idx))
                w.n_triggers += 1
                w.n_blocked += 1 if result.blocked else 0
                w.n_failures += 1 if result.failed else 0
                for s in result.signals:
                    w.n_signals += 1
                    w.edge_values.append(s.edge_score)
                    w.tiers.append(s.tier)
                    w.truth_conf.append(s.truth_confidence)

        ordered = [windows[i] for i in sorted(windows)]
        return RunResult(ordered, orch.metrics.to_dict(),
                         len(orch.paper_signals), orch.metrics.triggers_handled)

    # -- shadow vs paper + full report -------------------------------------
    def shadow_vs_paper(self, seed: int = 0,
                        drifts: Optional[List[DriftInjection]] = None,
                        n_matches: int = 1,
                        noise_std: float = 0.01) -> ShadowReport:
        paper_cfg = TimelineConfig(noise_std=0.0)
        shadow_cfg = TimelineConfig(noise_std=noise_std)
        paper = self.run(seed, drifts=None, n_matches=n_matches, config=paper_cfg)
        shadow = self.run(seed, drifts=drifts, n_matches=n_matches, config=shadow_cfg)
        divergence = ShadowPaperDivergence().compute(shadow.windows, paper.windows)
        return self._report(shadow, drifts or [], divergence, paper)

    def report(self, run: RunResult,
               drifts: Optional[List[DriftInjection]] = None) -> ShadowReport:
        return self._report(run, drifts or [], None, None)

    # -- assembly ----------------------------------------------------------
    def _report(self, run: RunResult, drifts: List[DriftInjection],
                divergence: Optional[DivergenceResult],
                paper: Optional[RunResult]) -> ShadowReport:
        health = self.kernel.score(run.windows)
        flags = self.detector.scan(run.windows)
        heatmap = self._drift_heatmap(run, drifts, paper)
        stability = self._stability_score(health, flags, divergence)
        return ShadowReport(stability, health, flags, heatmap, divergence, run)

    @staticmethod
    def _drift_heatmap(run: RunResult, drifts: List[DriftInjection],
                       paper: Optional[RunResult]) -> Dict[str, List[float]]:
        ticks = [w.index for w in run.windows]
        n = max(ticks) + 1 if ticks else 0
        rows: Dict[str, List[float]] = {
            "LIQUIDITY_SHOCK": [0.0] * n,
            "SHARP_MOVE": [0.0] * n,
            "API_DELAY": [0.0] * n,
        }
        for d in drifts:
            if 0 <= d.at_tick < n and d.type.value in rows:
                rows[d.type.value][d.at_tick] += abs(d.magnitude) or 1.0
        # observed impact = |shadow edge_mean - paper edge_mean| per tick
        impact = [0.0] * n
        if paper is not None:
            pmap = {w.index: w.edge_mean for w in paper.windows}
            for w in run.windows:
                impact[w.index] = round(abs(w.edge_mean - pmap.get(w.index, w.edge_mean)), 4)
        rows["OBSERVED_IMPACT"] = impact
        return rows

    @staticmethod
    def _stability_score(health: HealthScore, flags: List[Flag],
                         divergence: Optional[DivergenceResult]) -> float:
        score = health.composite
        score -= 10.0 * len(flags)                     # each silent failure penalizes
        if divergence is not None:
            score -= min(20.0, 100.0 * divergence.spg)  # divergence penalty (bounded)
        return round(max(0.0, min(100.0, score)), 2)


# ---------------------------------------------------------------------------
# Stress scenarios (load / outage)
# ---------------------------------------------------------------------------
def peak_day_report(runner: ShadowRunner, n_matches: int = 10, seed: int = 1) -> ShadowReport:
    """EPL-Saturday peak: many concurrent matches at one kickoff."""
    run = runner.run(seed, drifts=None, n_matches=n_matches)
    return runner.report(run)


def partial_outage_report(runner: ShadowRunner, seed: int = 2) -> ShadowReport:
    """A provider drops out across several mid-timeline ticks (partial feed)."""
    from .timeline import DriftType
    drifts = [DriftInjection(DriftType.API_DELAY, at_tick=t, provider="pinnacle")
              for t in (3, 4, 5)]
    run = runner.run(seed, drifts=drifts, n_matches=1)
    return runner.report(run, drifts)


def odds_burst_report(runner: ShadowRunner, seed: int = 3) -> ShadowReport:
    """Multi-provider spike: extra providers quoting simultaneously."""
    cfg = TimelineConfig(providers=["pinnacle", "bet365", "williamhill", "marathon", "betfair"])
    run = runner.run(seed, drifts=None, n_matches=1, config=cfg)
    return runner.report(run)
