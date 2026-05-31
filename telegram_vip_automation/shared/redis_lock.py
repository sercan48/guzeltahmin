"""Redis-based distributed lock using SET NX EX pattern."""

import logging
import uuid
from types import TracebackType
from typing import Self

import redis.asyncio as redis

logger = logging.getLogger(__name__)


class LockAcquisitionError(Exception):
    """Raised when a distributed lock cannot be acquired."""

    def __init__(self, lock_name: str) -> None:
        self.lock_name = lock_name
        super().__init__(f"Failed to acquire lock: {lock_name}")


class DistributedLock:
    """Distributed lock backed by Redis SET NX EX.

    Usage::

        async with DistributedLock(redis_url, "expire_check") as lock:
            # critical section
            ...

    If the lock is already held by another worker, ``LockAcquisitionError``
    is raised immediately (no blocking/spinning).

    Each lock instance generates a unique token so only the holder that
    acquired it can release it (owner-safe release via Lua script).
    """

    _RELEASE_LUA = """
    if redis.call("GET", KEYS[1]) == ARGV[1] then
        return redis.call("DEL", KEYS[1])
    else
        return 0
    end
    """

    def __init__(
        self,
        redis_url: str,
        lock_name: str,
        ttl: int = 300,
    ) -> None:
        self._redis_url = redis_url
        self._lock_name = lock_name
        self._key = f"lock:{lock_name}"
        self._ttl = ttl
        self._token: str = uuid.uuid4().hex
        self._client: redis.Redis | None = None
        self._acquired: bool = False

    async def _get_client(self) -> redis.Redis:
        if self._client is None:
            self._client = redis.from_url(
                self._redis_url, decode_responses=True
            )
        return self._client

    async def acquire(self) -> bool:
        """Attempt to acquire the lock. Returns True on success.

        Raises ``LockAcquisitionError`` if the lock is already held.
        """
        client = await self._get_client()
        result = await client.set(
            self._key, self._token, nx=True, ex=self._ttl
        )

        if result:
            self._acquired = True
            logger.info(
                "distributed_lock_acquired",
                extra={"lock": self._lock_name, "ttl": self._ttl},
            )
            return True

        logger.warning(
            "distributed_lock_held_by_another",
            extra={"lock": self._lock_name},
        )
        raise LockAcquisitionError(self._lock_name)

    async def release(self) -> None:
        """Release the lock only if we still own it (owner-safe)."""
        if not self._acquired:
            return

        client = await self._get_client()
        try:
            released = await client.eval(
                self._RELEASE_LUA, 1, self._key, self._token
            )
            if released:
                logger.info(
                    "distributed_lock_released",
                    extra={"lock": self._lock_name},
                )
            else:
                logger.warning(
                    "distributed_lock_release_mismatch",
                    extra={"lock": self._lock_name},
                )
        except Exception as exc:
            logger.error(
                "distributed_lock_release_error",
                extra={"lock": self._lock_name, "error": str(exc)},
            )
        finally:
            self._acquired = False

    async def _close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def __aenter__(self) -> Self:
        await self.acquire()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        await self.release()
        await self._close()
