"""M10.2 — Ingestion Bridge & Provider Activation Layer.

Connects M10.1 scheduler events to M2 Truth Store ingestion via a provider
abstraction, with bounded deterministic retry and append-only ingestion history.
Additive over M1-M10.1; no network, no ML/prediction/betting.
"""

from .providers import (
    OddsProvider,
    MockOddsProvider,
    ProviderQuote,
    ProviderOutcome,
    ProviderError,
)
from .bridge import (
    IngestionBridge,
    IngestionJob,
    JobResult,
    JobType,
    JobStatus,
)

__all__ = [
    "OddsProvider",
    "MockOddsProvider",
    "ProviderQuote",
    "ProviderOutcome",
    "ProviderError",
    "IngestionBridge",
    "IngestionJob",
    "JobResult",
    "JobType",
    "JobStatus",
]
