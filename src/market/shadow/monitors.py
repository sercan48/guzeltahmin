"""M7 — Shadow monitors: silent-failure detection, shadow/paper divergence,
and the System Health Kernel v1.

Pure analysis over per-window run statistics. No prediction logic, no execution.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional


@dataclass
class WindowStat:
    """Aggregated orchestrator behaviour for one timeline window (tick)."""
    index: int
    n_triggers: int = 0
    n_signals: int = 0
    n_blocked: int = 0
    n_failures: int = 0
    edge_values: List[float] = field(default_factory=list)
    tiers: List[str] = field(default_factory=list)
    truth_conf: List[float] = field(default_factory=list)

    @property
    def edge_mean(self) -> float:
        return sum(self.edge_values) / len(self.edge_values) if self.edge_values else 0.0

    @property
    def truth_conf_mean(self) -> float:
        return sum(self.truth_conf) / len(self.truth_conf) if self.truth_conf else 0.0


@dataclass
class Flag:
    type: str
    window_index: Optional[int]
    detail: str

    def to_dict(self) -> dict:
        return asdict(self)


def _mean(xs: List[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def _var(xs: List[float]) -> float:
    if len(xs) < 2:
        return 0.0
    m = _mean(xs)
    return sum((x - m) ** 2 for x in xs) / (len(xs) - 1)


# ---------------------------------------------------------------------------
# Silent failure detection
# ---------------------------------------------------------------------------
@dataclass
class SilentFailureConfig:
    min_total_signals: int = 1
    clv_collapse_threshold: float = 0.40   # truth-conf proxy floor near the close
    edge_stagnation_var: float = 1e-6      # below this, edges are "frozen"
    min_triggers_for_checks: int = 3


class SilentFailureDetector:
    def __init__(self, config: SilentFailureConfig = SilentFailureConfig()) -> None:
        self.cfg = config

    def scan(self, windows: List[WindowStat]) -> List[Flag]:
        flags: List[Flag] = []
        total_triggers = sum(w.n_triggers for w in windows)
        total_signals = sum(w.n_signals for w in windows)
        all_edges = [e for w in windows for e in w.edge_values]

        if total_triggers < self.cfg.min_triggers_for_checks:
            return flags

        # 1. no-signal bug: traffic flowed but nothing was ever emitted
        if total_signals < self.cfg.min_total_signals:
            flags.append(Flag("no_signal_bug", None,
                              f"{total_triggers} triggers, 0 signals"))

        # 2. silent CLV collapse: truth-confidence proxy decays below floor by the close
        confs = [w.truth_conf_mean for w in windows if w.truth_conf]
        if len(confs) >= 2 and confs[-1] < self.cfg.clv_collapse_threshold < confs[0]:
            flags.append(Flag("silent_clv_collapse", windows[-1].index,
                              f"truth-conf {confs[0]:.2f} -> {confs[-1]:.2f}"))

        # 3. edge stagnation: signals present but edge distribution is frozen
        if len(all_edges) >= 3 and _var(all_edges) < self.cfg.edge_stagnation_var:
            flags.append(Flag("edge_stagnation", None,
                              f"edge var {_var(all_edges):.2e} (frozen)"))

        # 4. orchestrator dead zone: traffic flowed across the whole run but the
        #    pipeline produced NOTHING observable anywhere (no signal, no block,
        #    no failure) -> the orchestrator is silently idle/broken. Run-level
        #    so an all-REJECT tick (legitimately 0 signals) is not a false flag.
        any_activity = any((w.n_signals or w.n_blocked or w.n_failures) for w in windows)
        if total_triggers > 0 and not any_activity:
            flags.append(Flag("orchestrator_dead_zone", None,
                              f"{total_triggers} triggers, no signal/block/failure anywhere"))
        return flags


# ---------------------------------------------------------------------------
# Shadow vs Paper divergence
# ---------------------------------------------------------------------------
@dataclass
class DivergenceResult:
    spg: float                  # Shadow-Paper Gap (mean |edge_mean diff| per window)
    cr: float                   # Consistency Rate (fraction of windows within epsilon)
    regime_drift: float         # stdev of per-window diffs (distributional drift)
    per_window_gap: List[float] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


class ShadowPaperDivergence:
    def __init__(self, epsilon: float = 0.02) -> None:
        self.epsilon = epsilon

    def compute(self, shadow: List[WindowStat], paper: List[WindowStat]) -> DivergenceResult:
        n = min(len(shadow), len(paper))
        gaps = [abs(shadow[i].edge_mean - paper[i].edge_mean) for i in range(n)]
        if not gaps:
            return DivergenceResult(0.0, 1.0, 0.0, [])
        spg = _mean(gaps)
        cr = sum(1 for g in gaps if g <= self.epsilon) / len(gaps)
        regime_drift = math.sqrt(_var(gaps))
        return DivergenceResult(spg, cr, regime_drift, gaps)


# ---------------------------------------------------------------------------
# System Health Kernel v1
# ---------------------------------------------------------------------------
@dataclass
class HealthConfig:
    weights: Dict[str, float] = field(default_factory=lambda: {
        "pipeline_uptime": 0.30,
        "signal_density": 0.25,
        "clv_stability": 0.25,
        "edge_entropy": 0.20,
    })
    signal_density_ref: float = 0.5     # signals/trigger that maps to full score
    clv_var_ref: float = 0.01           # edge-mean variance that maps to zero stability
    alert_threshold: float = 0.50       # subscore below this raises an alert


@dataclass
class HealthScore:
    composite: float                    # 0..100
    subscores: Dict[str, float]
    alerts: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


class SystemHealthKernel:
    def __init__(self, config: HealthConfig = HealthConfig()) -> None:
        self.cfg = config

    def score(self, windows: List[WindowStat]) -> HealthScore:
        total_triggers = sum(w.n_triggers for w in windows) or 1
        total_signals = sum(w.n_signals for w in windows)
        total_failures = sum(w.n_failures for w in windows)

        uptime = 1.0 - total_failures / total_triggers
        density = min(1.0, (total_signals / total_triggers) / self.cfg.signal_density_ref)
        clv_stability = 1.0 - min(1.0, _var([w.edge_mean for w in windows]) / self.cfg.clv_var_ref)
        entropy = self._tier_entropy([t for w in windows for t in w.tiers])

        subs = {
            "pipeline_uptime": max(0.0, uptime),
            "signal_density": max(0.0, density),
            "clv_stability": max(0.0, clv_stability),
            "edge_entropy": entropy,
        }
        w = self.cfg.weights
        composite = 100.0 * sum(w[k] * subs[k] for k in w)
        alerts = [f"{k} low ({subs[k]:.2f})" for k in subs
                  if subs[k] < self.cfg.alert_threshold]
        return HealthScore(round(composite, 2), {k: round(v, 4) for k, v in subs.items()}, alerts)

    @staticmethod
    def _tier_entropy(tiers: List[str]) -> float:
        """Normalized Shannon entropy of the tier mix in [0,1].

        0 => single tier (stagnation) or no signals; ~1 => diverse mix.
        """
        if not tiers:
            return 0.0
        counts: Dict[str, int] = {}
        for t in tiers:
            counts[t] = counts.get(t, 0) + 1
        k = len(counts)
        if k < 2:
            return 0.0
        n = len(tiers)
        h = -sum((c / n) * math.log(c / n) for c in counts.values())
        return h / math.log(k)
