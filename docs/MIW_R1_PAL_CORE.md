# MIW R1 — PAL Core (Provider Abstraction Layer) Implementation

> First step of the design->implementation transition. Goal: make ALL odds data sources interchangeable so that NO downstream module (model, CLV, threshold, portfolio) knows which provider is used. This document is STRICT infrastructure/interface-only: no business logic outside the interface layer; model/CLV/threshold layers are untouched. Code blocks are interface/skeleton level only.

## 1. Architecture Explanation

PAL is the single ingress for all MIW data. Downstream modules never see a provider SDK or a raw API response; they consume only a normalized `OddsRecord` stream.

Providers (FootballDataProvider, TheOddsAPIFreeProvider, APIFootballProvider) implement `OddsProviderInterface`. A `ProviderRegistry` (configured by `providers.yaml`) exposes a single `get_active()` entry point and performs automatic failover. The PAL boundary emits only `OddsRecord`; everything downstream (Model/CLV/Threshold/Portfolio) sits behind that boundary.

How the critical rule is enforced:
- Downstream takes an `OddsProviderInterface` reference only via `ProviderRegistry.get_active()` — it never imports a concrete provider class.
- Return type is always `OddsRecord` (or a list) — no provider-specific field leaks.
- Provider selection comes only from YAML config; hardcoding is forbidden.
- Result: adding a provider = a new adapter class + a YAML line; ZERO downstream change.

## 2. Class Design (interface layer)

### 2.1 Enums & Schema (Module 1)
```python
from enum import Enum
from datetime import datetime
from pydantic import BaseModel, Field

class SnapshotType(str, Enum):
    OPEN = "OPEN"
    T_24H = "T-24h"
    T_12H = "T-12h"
    T_6H  = "T-6h"
    T_1H  = "T-1h"
    CLOSE = "CLOSE"

class MarketType(str, Enum):
    ONE_X_TWO = "1X2"
    OVER_UNDER = "O/U"
    BTTS = "BTTS"
    AH = "ASIAN_HANDICAP"

class OddsRecord(BaseModel):
    """The single output contract of PAL. Every provider emits this."""
    match_id: str
    bookmaker: str            # canonical name (post-mapping)
    market: MarketType
    selection: str            # e.g. "HOME" / "OVER_2.5"
    odds: float               # decimal format, > 1.0
    timestamp: datetime       # UTC, timezone-aware
    snapshot_type: SnapshotType
    source_id: str            # which provider/feed (audit only)
    confidence_score: float = Field(ge=0.0, le=1.0)
```

### 2.2 Abstract Interface (Module 1)
```python
from abc import ABC, abstractmethod

class HealthStatus(BaseModel):
    healthy: bool
    latency_ms: float | None = None
    quota_remaining: int | None = None
    detail: str = ""

class OddsProviderInterface(ABC):
    """Contract all provider adapters must satisfy."""

    @abstractmethod
    def fetch_odds(self, match_id: str, market: MarketType) -> list[OddsRecord]:
        """Live/current odds. Returns normalized OddsRecord."""
        ...

    @abstractmethod
    def fetch_historical_odds(self, match_id: str) -> list[OddsRecord]:
        """All historical odds records for a match."""
        ...

    @abstractmethod
    def fetch_snapshot_odds(self, match_id: str, snapshot_type: SnapshotType) -> list[OddsRecord]:
        """Odds for a specific snapshot window (OPEN..CLOSE)."""
        ...

    @abstractmethod
    def validate_source_health(self) -> HealthStatus:
        """Health + quota + latency check (for failover decisions)."""
        ...
```

### 2.3 Adapter Skeletons (Module 2 — NO business logic)
Each adapter implements `OddsProviderInterface`, normalizes raw API format into `OddsRecord`, and embeds rate-limit/retry/graceful-fail. The code here is skeleton only; real fetch/parse logic is filled during implementation.
```python
class FootballDataProvider(OddsProviderInterface):
    source_id = "football_data"
    def __init__(self, client, normalizer, rate_limiter, breaker): ...
    def fetch_odds(self, match_id, market) -> list[OddsRecord]:
        raise NotImplementedError  # filled in R1.3
    # fetch_historical_odds / fetch_snapshot_odds / validate_source_health ...

class TheOddsAPIFreeProvider(OddsProviderInterface):
    source_id = "the_odds_api"
    # same contract, different normalization map

class APIFootballProvider(OddsProviderInterface):
    source_id = "api_football"
    # same contract, different normalization map
```

### 2.4 Provider Registry (Module 3)
```python
class ProviderRegistry:
    def register_provider(self, key: str, provider: OddsProviderInterface) -> None: ...
    def get_provider(self, key: str) -> OddsProviderInterface: ...
    def health_check_all(self) -> dict[str, HealthStatus]: ...
    def fallback_provider_selection(self) -> OddsProviderInterface:
        """Return the first HEALTHY provider in primary->fallback chain."""
        ...
    def get_active(self) -> OddsProviderInterface:
        """The only entry point downstream sees."""
        ...
```
- primary + ordered fallback chain loaded from YAML.
- `get_active()` tries primary first; if circuit breaker is open or health is bad, it advances down the chain (automatic failover).

## 3. Provider Flow Diagram
```
Downstream --> Registry.get_active().fetch_snapshot_odds(match, T_1H)
  Registry --> CircuitBreaker: primary open?
    if breaker CLOSED (healthy): Primary.fetch -> list[OddsRecord]
    else (open/error):          Fallback.fetch -> list[OddsRecord]
  Registry --> Downstream: list[OddsRecord] (provider identity hidden)
```

## 4. Normalization Rules + Config (Modules 5 & 4)

### 4.1 Strict Normalization Contract
| Dimension | Rule |
|---|---|
| Odds format | Always decimal; American/fractional converted to decimal; validate odds > 1.0 |
| Time | Always UTC, timezone-aware; local/Unix epoch -> UTC; naive datetime rejected |
| Bookmaker name | Mapped to canonical ("Bet365","bet 365","B365" -> bet365); unknown -> quarantine + low confidence |
| Market | Mapped to MarketType enum ("Match Odds","h2h" -> 1X2; "Totals" -> O/U) |
| Selection | Standard token ("1"/"X"/"2" -> HOME/DRAW/AWAY; "Over 2.5" -> OVER_2.5) |
| Snapshot | Collection time rounded to nearest window relative to kickoff (OPEN/T-24h/.../CLOSE) |

One-way rule: normalization happens only inside the adapter; once `OddsRecord` crosses the PAL boundary, no provider-specific transformation is ever applied.

### 4.2 YAML Config Layer (Module 4)
```yaml
providers:
  primary: football_data
  fallback:
    - the_odds_api
    - api_football

health:
  max_latency_ms: 4000
  min_quota_remaining: 25

breaker:
  failure_threshold: 5      # consecutive failures
  cooldown_seconds: 120

retry:
  max_attempts: 4
  base_delay_ms: 250        # exponential backoff base
  jitter: true
```
- Hardcoded provider selection is FORBIDDEN. Providers are read from YAML; the file can be swapped and reloaded at runtime for full runtime switching.
- Config is loaded and validated into a `PalConfig` pydantic model (schema error -> startup refused).

## 5. Failure Handling Model (Module 6)
| Mechanism | Behavior |
|---|---|
| Exponential backoff retry | delay = base * 2^(attempt-1) + jitter; up to max_attempts; only on transient errors (5xx, timeout, 429) |
| Circuit breaker (per provider) | failure_threshold consecutive failures -> breaker OPEN; provider skipped during cooldown; then HALF-OPEN trial |
| Degraded mode fallback | all primary attempts fail -> switch to fallback chain; if all fail -> empty result + stale/degraded flag (no crash) |
| Partial data acceptance | if some markets/bookmakers missing, valid OddsRecords accepted; missing flagged; schema-invalid records dropped |

Breaker states: CLOSED -> (threshold exceeded) -> OPEN -> (cooldown) -> HALF_OPEN -> CLOSED on success / OPEN on failure.

Principle: every failure ends in safe fallback or safe empty-result; PAL never throws an exception that crashes downstream (graceful degradation).

## 6. Test Strategy (Module 7)
| Test | How verified |
|---|---|
| Provider swap (no code) | Change only primary in YAML; downstream output produces the same OddsRecord schema; downstream code unchanged |
| Schema validation | Every adapter output passes OddsRecord pydantic validation; required fields, decimal>1.0, UTC-aware, confidence in [0,1] |
| Fallback activation | Force primary to fail (mock); verify registry switches to fallback |
| Failure simulation | Mock 5xx/timeout/429; verify retry+backoff+breaker behavior |
| Rate limit handling | Mock 429 + Retry-After; verify rate limiter waits/queues |
| Contract test | A single parametrized test applied to all adapters (same contract -> same schema) |

All tests run on recorded fixtures (saved raw API samples) — no network dependency, deterministic.

## 7. Implementation Roadmap (step-by-step)
| Step | Work | Output |
|---|---|---|
| R1.1 | Schema + enums + ABC + HealthStatus + PalConfig | pal/contracts.py, pal/interface.py |
| R1.2 | Normalizer helpers (odds/time/bookmaker/market maps) | pal/normalize.py + mapping tables |
| R1.3 | 3 adapter skeletons -> wire normalization (minimal logic) | pal/providers/*.py |
| R1.4 | Rate limiter + retry/backoff + circuit breaker helpers | pal/resilience.py |
| R1.5 | ProviderRegistry + failover + get_active() | pal/registry.py |
| R1.6 | YAML loader + runtime reload | pal/config.py, providers.yaml |
| R1.7 | Contract + fixture + failure/fallback tests | tests/pal/* |
| R1.8 | Downstream integration point: inject only registry.get_active() | single wiring point |

Definition of Done: changing primary in YAML yields the same-schema data flow without any downstream change; all contract + failover + rate-limit tests green.

---
Summary: PAL Core puts all odds sources behind one `OddsProviderInterface` + `OddsRecord` contract; selection comes from YAML, failure handling (retry/backoff, per-provider circuit breaker, degraded fallback, partial data) is solved inside, and downstream never knows the provider. This document is strict infrastructure-only: no business logic outside the interface, model/CLV/threshold untouched. Next: R2 — Historical Odds Warehouse MVP.
