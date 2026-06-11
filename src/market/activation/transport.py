"""M10.3 — Production Transport Layer.

A network-agnostic transport stack that production provider adapters consume via
dependency injection. The CORE system never imports HTTP: the only network seam
is a swappable ``HttpClient`` (default = NullHttpClient, raises). Everything here
is deterministic when given a fake HttpClient + injected clock/sleeper, so
offline replay determinism is preserved.

Pieces (all in src/market/activation/, no changes elsewhere):
  1. Transport            — orchestrates a request through the stack
  2. RetryPolicy          — bounded retries
  3. exponential backoff  — delay = min(base*factor^n (+jitter), max); honors Retry-After
  4. RateLimiter          — token bucket (injected monotonic)
  5. SecretProvider       — env/static; values redacted from all logs
  6. ProviderHealth       — latency / success / failure / breaker state
  7. CircuitBreaker       — CLOSED/OPEN/HALF_OPEN fast-fail
  8. RequestAuditLog      — append-only, SECRET-REDACTED request trail

Adapters keep their existing ``transport: Callable[[fixture_id, market], dict]``
DI seam; ``Transport.binding(endpoint)`` produces that callable backed by the
production stack — so PinnacleProvider/BetfairProvider are unchanged.
"""

from __future__ import annotations

import time
import urllib.parse
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Dict, List, Optional

from .providers import ProviderError

_REDACT = "***"
_SECRET_HEADER_KEYS = {"authorization", "x-api-key", "x-auth-token"}


# ---------------------------------------------------------------------------
# HTTP seam (the ONLY network boundary; swappable)
# ---------------------------------------------------------------------------
@dataclass
class HttpResponse:
    status_code: int
    body: object                       # parsed dict (or str)
    headers: Dict[str, str] = field(default_factory=dict)


@dataclass
class RequestSpec:
    method: str
    url: str
    params: Dict[str, str] = field(default_factory=dict)
    headers: Dict[str, str] = field(default_factory=dict)


class HttpClient(ABC):
    @abstractmethod
    def request(self, method: str, url: str, params: Dict[str, str],
                headers: Dict[str, str]) -> HttpResponse:
        ...


class NullHttpClient(HttpClient):
    """Default: no network. Guarantees the core stays network-agnostic."""

    def request(self, method, url, params, headers) -> HttpResponse:
        raise ConnectionError("NullHttpClient: network disabled")


class FakeHttpClient(HttpClient):
    """Deterministic test client. ``script`` is a list of HttpResponse or
    Exception; popped per call (last element repeats)."""

    def __init__(self, script: List[object]) -> None:
        self._script = list(script)
        self.calls = 0
        self.last_request: Optional[dict] = None   # for assertions (e.g. auth header)

    def request(self, method, url, params, headers) -> HttpResponse:
        self.calls += 1
        self.last_request = {"method": method, "url": url,
                             "params": dict(params), "headers": dict(headers)}
        item = self._script[0] if len(self._script) == 1 else self._script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class UrllibHttpClient(HttpClient):  # pragma: no cover - real network, not tested
    """Real stdlib HTTP implementation (swappable). Never invoked in tests."""

    def __init__(self, timeout: float = 10.0) -> None:
        self.timeout = timeout

    def request(self, method, url, params, headers) -> HttpResponse:
        import json
        import urllib.request
        full = url + ("?" + urllib.parse.urlencode(params) if params else "")
        req = urllib.request.Request(full, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read().decode("utf-8")
                body = json.loads(raw) if raw else {}
                return HttpResponse(resp.status, body, dict(resp.headers))
        except urllib.error.HTTPError as e:
            raw = e.read().decode("utf-8") if e.fp else ""
            try:
                body = json.loads(raw) if raw else {}
            except ValueError:
                body = raw
            return HttpResponse(e.code, body, dict(e.headers or {}))
        except urllib.error.URLError as e:
            raise ConnectionError(str(e))


# ---------------------------------------------------------------------------
# Secrets (never logged)
# ---------------------------------------------------------------------------
class SecretProvider(ABC):
    @abstractmethod
    def get(self, name: str) -> str:
        ...

    def has(self, name: str) -> bool:
        try:
            self.get(name)
            return True
        except KeyError:
            return False


class StaticSecretProvider(SecretProvider):
    def __init__(self, secrets: Dict[str, str]) -> None:
        self._s = dict(secrets)

    def get(self, name: str) -> str:
        if name not in self._s:
            raise KeyError(name)
        return self._s[name]


class EnvSecretProvider(SecretProvider):
    def get(self, name: str) -> str:
        import os
        if name not in os.environ:
            raise KeyError(name)
        return os.environ[name]


@dataclass
class AuthConfig:
    secret_key: str                 # name resolved via SecretProvider
    header: Optional[str] = None    # e.g. "Authorization"
    param: Optional[str] = None     # e.g. "apiKey"
    prefix: str = ""                # e.g. "Bearer "


# ---------------------------------------------------------------------------
# Retry / backoff
# ---------------------------------------------------------------------------
@dataclass
class RetryPolicy:
    max_retries: int = 3
    base_delay: float = 0.5
    factor: float = 2.0
    max_delay: float = 8.0
    jitter: float = 0.0             # deterministic by default (no jitter)

    def backoff(self, attempt: int, retry_after: Optional[float] = None) -> float:
        if retry_after is not None:
            return min(retry_after, self.max_delay)
        return min(self.base_delay * (self.factor ** attempt) + self.jitter, self.max_delay)


# ---------------------------------------------------------------------------
# Rate limiter (token bucket)
# ---------------------------------------------------------------------------
class RateLimiter:
    def __init__(self, capacity: float, refill_per_sec: float,
                 monotonic: Callable[[], float] = time.monotonic) -> None:
        self.capacity = float(capacity)
        self.rate = float(refill_per_sec)
        self.monotonic = monotonic
        self.tokens = float(capacity)
        self.last = monotonic()

    def acquire(self) -> float:
        """Return seconds the caller must wait (0 if a token is available)."""
        now = self.monotonic()
        self.tokens = min(self.capacity, self.tokens + (now - self.last) * self.rate)
        self.last = now
        if self.tokens >= 1.0:
            self.tokens -= 1.0
            return 0.0
        wait = (1.0 - self.tokens) / self.rate if self.rate > 0 else 0.0
        self.tokens = 0.0
        return wait


# ---------------------------------------------------------------------------
# Circuit breaker
# ---------------------------------------------------------------------------
class CircuitState(str, Enum):
    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"


class CircuitBreaker:
    def __init__(self, failure_threshold: int = 5, cooldown: float = 30.0,
                 monotonic: Callable[[], float] = time.monotonic) -> None:
        self.failure_threshold = failure_threshold
        self.cooldown = cooldown
        self.monotonic = monotonic
        self.state = CircuitState.CLOSED
        self.failures = 0
        self.opened_at = 0.0

    def allow(self) -> bool:
        if self.state == CircuitState.OPEN:
            if self.monotonic() - self.opened_at >= self.cooldown:
                self.state = CircuitState.HALF_OPEN
                return True
            return False
        return True

    def record_success(self) -> None:
        self.failures = 0
        self.state = CircuitState.CLOSED

    def record_failure(self) -> None:
        self.failures += 1
        if self.state == CircuitState.HALF_OPEN or self.failures >= self.failure_threshold:
            self.state = CircuitState.OPEN
            self.opened_at = self.monotonic()


# ---------------------------------------------------------------------------
# Health + audit
# ---------------------------------------------------------------------------
class ProviderHealth:
    def __init__(self) -> None:
        self.success = 0
        self.failure = 0
        self.total_latency_ms = 0.0
        self.last_error = ""

    def record(self, ok: bool, latency_ms: float, error: str = "") -> None:
        if ok:
            self.success += 1
        else:
            self.failure += 1
            self.last_error = error
        self.total_latency_ms += latency_ms

    def snapshot(self, breaker_state: str) -> dict:
        n = self.success + self.failure
        return {
            "success": self.success, "failure": self.failure,
            "avg_latency_ms": round(self.total_latency_ms / n, 3) if n else None,
            "last_error": self.last_error, "breaker_state": breaker_state,
        }


class RequestAuditLog:
    """Append-only, SECRET-REDACTED request trail."""

    def __init__(self) -> None:
        self._entries: List[dict] = []

    def add(self, method: str, url_redacted: str, status, attempt: int,
            outcome: str, latency_ms: float) -> None:
        self._entries.append({
            "seq": len(self._entries), "method": method, "url": url_redacted,
            "status": status, "attempt": attempt, "outcome": outcome,
            "latency_ms": round(latency_ms, 3),
        })

    def entries(self) -> List[dict]:
        return list(self._entries)


# ---------------------------------------------------------------------------
# Transport orchestrator
# ---------------------------------------------------------------------------
class Transport:
    def __init__(self, http_client: Optional[HttpClient] = None,
                 secret_provider: Optional[SecretProvider] = None,
                 auth: Optional[AuthConfig] = None,
                 retry: RetryPolicy = RetryPolicy(),
                 rate_limiter: Optional[RateLimiter] = None,
                 breaker: Optional[CircuitBreaker] = None,
                 monotonic: Callable[[], float] = time.monotonic,
                 sleeper: Callable[[float], None] = time.sleep,
                 audit: Optional[RequestAuditLog] = None) -> None:
        self.http = http_client or NullHttpClient()
        self.secrets = secret_provider
        self.auth = auth
        self.retry = retry
        self.rate = rate_limiter
        self.breaker = breaker or CircuitBreaker(monotonic=monotonic)
        self.monotonic = monotonic
        self.sleeper = sleeper
        self.health = ProviderHealth()
        self.audit = audit or RequestAuditLog()

    # -- public -------------------------------------------------------------
    def request(self, spec: RequestSpec) -> dict:
        if not self.breaker.allow():
            raise ProviderError("circuit breaker OPEN")
        if self.rate is not None:
            wait = self.rate.acquire()
            if wait > 0:
                self.sleeper(wait)

        params, headers, secret_value = self._inject_auth(spec)
        url_red = self._redact_url(spec.url, params, secret_value)

        last_err = ""
        for attempt in range(self.retry.max_retries + 1):
            t0 = self.monotonic()
            try:
                resp = self.http.request(spec.method, spec.url, params, headers)
            except Exception as exc:                       # network/transport error
                latency = (self.monotonic() - t0) * 1000
                self.health.record(False, latency, str(exc))
                self.audit.add(spec.method, url_red, "ERR", attempt, "network_error", latency)
                self.breaker.record_failure()
                last_err = str(exc)
                if attempt < self.retry.max_retries:
                    self.sleeper(self.retry.backoff(attempt))
                    continue
                raise ProviderError(f"transport network error: {last_err}")

            latency = (self.monotonic() - t0) * 1000
            sc = resp.status_code
            if 200 <= sc < 300:
                self.health.record(True, latency)
                self.audit.add(spec.method, url_red, sc, attempt, "success", latency)
                self.breaker.record_success()
                return resp.body if isinstance(resp.body, dict) else {"raw": resp.body}
            if sc == 429 or 500 <= sc < 600:               # retryable
                self.health.record(False, latency, f"status {sc}")
                self.audit.add(spec.method, url_red, sc, attempt, "retryable", latency)
                self.breaker.record_failure()
                last_err = f"status {sc}"
                if attempt < self.retry.max_retries:
                    ra = self._retry_after(resp)
                    self.sleeper(self.retry.backoff(attempt, ra))
                    continue
                raise ProviderError(f"transport exhausted: {last_err}")
            # 4xx (non-retryable)
            self.health.record(False, latency, f"status {sc}")
            self.audit.add(spec.method, url_red, sc, attempt, "client_error", latency)
            self.breaker.record_failure()
            raise ProviderError(f"transport client error: status {sc}")

    def binding(self, endpoint: Callable[[str, str], RequestSpec]
                ) -> Callable[[str, str], dict]:
        """Produce the adapter-facing transport callable (fixture_id, market)->dict."""
        def _call(fixture_id: str, market: str) -> dict:
            return self.request(endpoint(fixture_id, market))
        return _call

    def health_snapshot(self) -> dict:
        return self.health.snapshot(self.breaker.state.value)

    # -- internals ----------------------------------------------------------
    def _inject_auth(self, spec: RequestSpec):
        params = dict(spec.params)
        headers = dict(spec.headers)
        secret_value = None
        if self.auth is not None and self.secrets is not None:
            secret_value = self.secrets.get(self.auth.secret_key)
            token = f"{self.auth.prefix}{secret_value}"
            if self.auth.header:
                headers[self.auth.header] = token
            if self.auth.param:
                params[self.auth.param] = secret_value
        return params, headers, secret_value

    def _redact_url(self, url: str, params: Dict[str, str], secret_value) -> str:
        # audit string only (never the real request) — kept human-readable so the
        # redaction marker stays visible; secret values are replaced, not encoded.
        pairs = []
        for k, v in params.items():
            if (self.auth and k == self.auth.param) or (secret_value and v == secret_value):
                v = _REDACT
            pairs.append(f"{k}={v}")
        return url + ("?" + "&".join(pairs) if pairs else "")

    @staticmethod
    def _retry_after(resp: HttpResponse) -> Optional[float]:
        ra = resp.headers.get("Retry-After") or resp.headers.get("retry-after")
        try:
            return float(ra) if ra is not None else None
        except (TypeError, ValueError):
            return None
