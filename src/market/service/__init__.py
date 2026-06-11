"""PHASE-LIVE L3/L4 — Runtime Service & Deployment Harness.

Wires the validated M1-M11 pipeline into a continuously-running production
service. Additive: zero changes to M1-M10.3 behaviour.
"""

from .config import RuntimeConfig, ProviderConfig, TelegramConfig, SchedulerConfig
from .health import HealthMonitor, HealthSnapshot
from .lock import SingleInstanceLock
from .monitor import (
    CompletenessTracker,
    FeedMonitor,
    LatencyTracker,
    LatencySample,
    MonitoringProvider,
    TruthConfidenceTracker,
)
from .publisher import TelegramPublisher, SignalFormatter, PublishResult
from .report import (
    DailySummary,
    ReadinessScore,
    ReplayVerifier,
    SettlementRecord,
    SettlementVerifier,
)
from .runtime import ServiceRuntime, IterationSummary, build_runtime
from .validation import (
    ValidationHarness,
    build_validation_harness,
    make_l4_dry_run_profile,
)

__all__ = [
    # L3 — config
    "RuntimeConfig",
    "ProviderConfig",
    "TelegramConfig",
    "SchedulerConfig",
    # L3 — health
    "HealthMonitor",
    "HealthSnapshot",
    # L3 — lock
    "SingleInstanceLock",
    # L3 — publisher
    "TelegramPublisher",
    "SignalFormatter",
    "PublishResult",
    # L3 — runtime
    "ServiceRuntime",
    "IterationSummary",
    "build_runtime",
    # L4 — monitor
    "LatencySample",
    "LatencyTracker",
    "CompletenessTracker",
    "TruthConfidenceTracker",
    "FeedMonitor",
    "MonitoringProvider",
    # L4 — report
    "SettlementRecord",
    "SettlementVerifier",
    "ReplayVerifier",
    "DailySummary",
    "ReadinessScore",
    # L4 — validation
    "ValidationHarness",
    "build_validation_harness",
    "make_l4_dry_run_profile",
]
