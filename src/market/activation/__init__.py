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
from .fixture_map import FixtureMap
from .adapters import PinnacleProvider, BetfairProvider
from .transport import (
    Transport,
    RequestSpec,
    HttpResponse,
    HttpClient,
    NullHttpClient,
    FakeHttpClient,
    UrllibHttpClient,
    SecretProvider,
    StaticSecretProvider,
    EnvSecretProvider,
    AuthConfig,
    RetryPolicy,
    RateLimiter,
    CircuitBreaker,
    CircuitState,
    ProviderHealth,
    RequestAuditLog,
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
    "FixtureMap",
    "PinnacleProvider",
    "BetfairProvider",
    "Transport",
    "RequestSpec",
    "HttpResponse",
    "HttpClient",
    "NullHttpClient",
    "FakeHttpClient",
    "UrllibHttpClient",
    "SecretProvider",
    "StaticSecretProvider",
    "EnvSecretProvider",
    "AuthConfig",
    "RetryPolicy",
    "RateLimiter",
    "CircuitBreaker",
    "CircuitState",
    "ProviderHealth",
    "RequestAuditLog",
    "IngestionBridge",
    "IngestionJob",
    "JobResult",
    "JobType",
    "JobStatus",
]
