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
from .pinnacle_live import (
    PinnacleLiveProvider,
    make_pinnacle_provider,
    parse_pinnacle_snapshot,
    parse_pinnacle_settled,
)
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
    "PinnacleLiveProvider",
    "make_pinnacle_provider",
    "parse_pinnacle_snapshot",
    "parse_pinnacle_settled",
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
