"""PHASE-LIVE L3 — Runtime Service & Deployment Harness.

Wires the validated M1-M11 pipeline into a continuously-running production
service. Additive: zero changes to M1-M10.3 behaviour.
"""

from .config import RuntimeConfig, ProviderConfig, TelegramConfig, SchedulerConfig
from .health import HealthMonitor, HealthSnapshot
from .lock import SingleInstanceLock
from .publisher import TelegramPublisher, SignalFormatter, PublishResult
from .runtime import ServiceRuntime, IterationSummary, build_runtime

__all__ = [
    "RuntimeConfig",
    "ProviderConfig",
    "TelegramConfig",
    "SchedulerConfig",
    "HealthMonitor",
    "HealthSnapshot",
    "SingleInstanceLock",
    "TelegramPublisher",
    "SignalFormatter",
    "PublishResult",
    "ServiceRuntime",
    "IterationSummary",
    "build_runtime",
]
