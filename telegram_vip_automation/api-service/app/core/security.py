"""FastAPI security dependency for HMAC signature verification with replay protection."""

import logging
from typing import Optional

from fastapi import Depends, HTTPException, Request, status

from app.core.config import settings

try:
    import redis.asyncio as aioredis
except ImportError:
    aioredis = None  # type: ignore[assignment]

from shared.security import verify_signature

logger = logging.getLogger(__name__)

_redis_client: Optional["aioredis.Redis"] = None  # type: ignore[name-defined]


async def _get_redis() -> Optional["aioredis.Redis"]:  # type: ignore[name-defined]
    """Lazy-init Redis connection for nonce storage."""
    global _redis_client
    if aioredis is None:
        return None
    if _redis_client is not None:
        return _redis_client
    if not settings.REDIS_URL:
        return None
    try:
        _redis_client = aioredis.from_url(
            settings.REDIS_URL, decode_responses=True
        )
        await _redis_client.ping()
        return _redis_client
    except Exception as exc:
        logger.warning("Redis unavailable for nonce store: %s", exc)
        _redis_client = None
        return None


async def _check_and_store_nonce(client_id: str, timestamp: str) -> bool:
    """Return True if nonce is fresh (not replayed). Store it with 120s TTL.

    If Redis is unavailable, log a warning and allow the request (no nonce check).
    """
    redis = await _get_redis()
    if redis is None:
        logger.warning("Nonce check skipped – Redis not available")
        return True

    nonce_key = f"nonce:{client_id}:{timestamp}"
    try:
        already_seen = await redis.set(nonce_key, "1", nx=True, ex=120)
        # redis SET NX returns True if the key was set (i.e. NOT seen before)
        if not already_seen:
            # Key already existed or set failed → replay
            return False
        return True
    except Exception as exc:
        logger.warning("Redis nonce check failed: %s", exc)
        return True


async def verify_hmac_signature(request: Request) -> None:
    """FastAPI dependency that enforces HMAC-SHA256 authentication on internal endpoints.

    Required headers:
        X-Signature  – hex HMAC-SHA256 digest
        X-Timestamp  – Unix timestamp (string)
        X-Client-ID  – calling service identifier (e.g. bot-service)
    """
    signature = request.headers.get("X-Signature")
    timestamp = request.headers.get("X-Timestamp")
    client_id = request.headers.get("X-Client-ID")

    if not signature or not timestamp or not client_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing authentication headers (X-Signature, X-Timestamp, X-Client-ID)",
        )

    # Read raw body (cached by Starlette after first read)
    raw_body = await request.body()
    body_str = raw_body.decode("utf-8") if raw_body else ""

    # Verify HMAC
    is_valid, error_msg = verify_signature(
        secret=settings.HMAC_SECRET,
        method=request.method,
        path=request.url.path,
        body=body_str,
        timestamp=timestamp,
        client_id=client_id,
        signature=signature,
    )
    if not is_valid:
        logger.warning(
            "HMAC verification failed for client=%s path=%s: %s",
            client_id,
            request.url.path,
            error_msg,
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Authentication failed: {error_msg}",
        )

    # Replay protection via nonce
    if not await _check_and_store_nonce(client_id, timestamp):
        logger.warning(
            "Replay attack detected for client=%s timestamp=%s",
            client_id,
            timestamp,
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Replay attack detected: duplicate request",
        )
