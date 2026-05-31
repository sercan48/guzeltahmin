"""Resilient HTTP client with retry and circuit breaker patterns."""

import asyncio
import time
import logging
from enum import Enum
from typing import Any, Optional

import httpx

logger = logging.getLogger(__name__)


class CircuitBreakerOpen(Exception):
    """Raised when the circuit breaker is in open state."""

    def __init__(self, remaining_seconds: float) -> None:
        self.remaining_seconds = remaining_seconds
        super().__init__(
            f"Circuit breaker is open. Retry after {remaining_seconds:.1f}s"
        )


class CircuitState(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class _CircuitBreaker:
    """Simple circuit breaker tracking consecutive failures."""

    def __init__(self, threshold: int = 5, timeout: float = 30.0) -> None:
        self._threshold = threshold
        self._timeout = timeout
        self._failure_count: int = 0
        self._last_failure_time: float = 0.0
        self._state = CircuitState.CLOSED

    @property
    def state(self) -> CircuitState:
        if self._state == CircuitState.OPEN:
            elapsed = time.monotonic() - self._last_failure_time
            if elapsed >= self._timeout:
                self._state = CircuitState.HALF_OPEN
                logger.info(
                    "circuit_breaker_half_open",
                    extra={"elapsed": f"{elapsed:.1f}s"},
                )
        return self._state

    @property
    def remaining_timeout(self) -> float:
        if self._state != CircuitState.OPEN:
            return 0.0
        elapsed = time.monotonic() - self._last_failure_time
        return max(0.0, self._timeout - elapsed)

    def record_success(self) -> None:
        if self._state in (CircuitState.HALF_OPEN, CircuitState.CLOSED):
            self._failure_count = 0
            self._state = CircuitState.CLOSED
            logger.debug("circuit_breaker_closed after successful request")

    def record_failure(self) -> None:
        self._failure_count += 1
        self._last_failure_time = time.monotonic()

        if self._failure_count >= self._threshold:
            self._state = CircuitState.OPEN
            logger.warning(
                "circuit_breaker_opened",
                extra={
                    "failure_count": self._failure_count,
                    "timeout": f"{self._timeout}s",
                },
            )

    def check(self) -> None:
        """Raise if circuit is open. Allow exactly one probe in half-open."""
        current = self.state
        if current == CircuitState.OPEN:
            raise CircuitBreakerOpen(self.remaining_timeout)
        # HALF_OPEN and CLOSED both allow through


class ResilientHttpClient:
    """Async HTTP client with exponential backoff retry and circuit breaker.

    Retries only on 5xx responses and connection/transport errors.
    4xx errors are returned immediately without retry.
    """

    def __init__(
        self,
        base_url: str,
        default_headers: dict[str, str] | None = None,
        timeout: float = 10.0,
        max_retries: int = 3,
        circuit_threshold: int = 5,
        circuit_timeout: float = 30.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._default_headers = default_headers or {}
        self._timeout = timeout
        self._max_retries = max_retries
        self._cb = _CircuitBreaker(
            threshold=circuit_threshold, timeout=circuit_timeout
        )

    @property
    def circuit_state(self) -> CircuitState:
        return self._cb.state

    async def request(
        self,
        method: str,
        path: str,
        json_data: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> Optional[dict[str, Any]]:
        """Execute an HTTP request with retry + circuit breaker.

        Returns parsed JSON dict on success, None on non-retryable failure.
        Raises CircuitBreakerOpen if circuit is open.
        """
        self._cb.check()

        url = f"{self._base_url}{path}"
        req_headers = {**self._default_headers, **(headers or {})}
        last_error: Exception | None = None

        for attempt in range(1, self._max_retries + 1):
            try:
                response = await self._do_request(
                    method, url, req_headers, json_data
                )
                return self._handle_response(response, method, path, attempt)

            except httpx.HTTPStatusError as exc:
                status = exc.response.status_code

                if 400 <= status < 500:
                    # Client errors are not retryable
                    logger.warning(
                        "http_client_error",
                        extra={
                            "method": method,
                            "path": path,
                            "status": status,
                            "body": exc.response.text[:500],
                        },
                    )
                    # Not a server fault → don't affect circuit breaker
                    return None

                # 5xx — retryable
                last_error = exc
                self._cb.record_failure()
                logger.warning(
                    "http_server_error_retrying",
                    extra={
                        "method": method,
                        "path": path,
                        "status": status,
                        "attempt": attempt,
                        "max_retries": self._max_retries,
                    },
                )

            except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout,
                    httpx.WriteTimeout, httpx.PoolTimeout, OSError) as exc:
                last_error = exc
                self._cb.record_failure()
                logger.warning(
                    "http_connection_error_retrying",
                    extra={
                        "method": method,
                        "path": path,
                        "error": str(exc),
                        "attempt": attempt,
                        "max_retries": self._max_retries,
                    },
                )

            except Exception as exc:
                # Unexpected errors — don't retry
                self._cb.record_failure()
                logger.error(
                    "http_unexpected_error",
                    extra={
                        "method": method,
                        "path": path,
                        "error": str(exc),
                        "type": type(exc).__name__,
                    },
                )
                return None

            # Wait before next retry (exponential backoff: 2, 4, 8 ...)
            if attempt < self._max_retries:
                delay = 2 ** attempt  # 2s, 4s, 8s
                logger.info(
                    "http_retry_backoff",
                    extra={"delay": f"{delay}s", "next_attempt": attempt + 1},
                )
                await asyncio.sleep(delay)

        # All retries exhausted
        logger.error(
            "http_all_retries_exhausted",
            extra={
                "method": method,
                "path": path,
                "attempts": self._max_retries,
                "last_error": str(last_error),
            },
        )
        return None

    async def _do_request(
        self,
        method: str,
        url: str,
        headers: dict[str, str],
        json_data: dict[str, Any] | None,
    ) -> httpx.Response:
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.request(
                method, url, headers=headers, json=json_data
            )
            response.raise_for_status()
            return response

    def _handle_response(
        self,
        response: httpx.Response,
        method: str,
        path: str,
        attempt: int,
    ) -> dict[str, Any] | None:
        self._cb.record_success()
        if attempt > 1:
            logger.info(
                "http_request_succeeded_after_retry",
                extra={"method": method, "path": path, "attempt": attempt},
            )
        return response.json()
