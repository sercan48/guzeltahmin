"""M7 — Shadow Run & Continuous Simulation Layer.

Drives the existing M5 orchestrator over simulated T-72h->CLOSE timelines with
deterministic seeds + stochastic noise + drift injection, and monitors runtime
health. Simulation + orchestration only: no prediction logic, no execution,
M1-M6 unchanged.
"""

from .timeline import (
    DriftType, DriftInjection, TimelineConfig, generate_timeline, DEFAULT_TICKS,
)
from .monitors import (
    WindowStat, Flag, SilentFailureDetector, SilentFailureConfig,
    ShadowPaperDivergence, DivergenceResult,
    SystemHealthKernel, HealthConfig, HealthScore,
)
from .runner import (
    ShadowRunner, RunResult, ShadowReport, default_sim_model,
    peak_day_report, partial_outage_report, odds_burst_report,
)

__all__ = [
    "DriftType", "DriftInjection", "TimelineConfig", "generate_timeline", "DEFAULT_TICKS",
    "WindowStat", "Flag", "SilentFailureDetector", "SilentFailureConfig",
    "ShadowPaperDivergence", "DivergenceResult",
    "SystemHealthKernel", "HealthConfig", "HealthScore",
    "ShadowRunner", "RunResult", "ShadowReport", "default_sim_model",
    "peak_day_report", "partial_outage_report", "odds_burst_report",
]
