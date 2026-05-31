"""HMAC-SHA256 request signing and verification for inter-service authentication."""

import hashlib
import hmac
import time
from typing import Tuple


TIMESTAMP_TOLERANCE_SECONDS = 60


def _hash_body(body: str) -> str:
    """SHA-256 hash of the request body."""
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _build_canonical_string(
    method: str, path: str, timestamp: str, client_id: str, body: str
) -> str:
    """Build the canonical string to sign.

    Format: {METHOD}\n{path}\n{timestamp}\n{client_id}\n{sha256(body)}
    """
    body_hash = _hash_body(body)
    return f"{method.upper()}\n{path}\n{timestamp}\n{client_id}\n{body_hash}"


def generate_signature(
    secret: str,
    method: str,
    path: str,
    body: str,
    timestamp: str,
    client_id: str,
) -> str:
    """Generate HMAC-SHA256 signature for an outgoing request.

    Args:
        secret: Shared HMAC secret.
        method: HTTP method (GET, POST, etc.).
        path: Request path (e.g. /api/v1/payments/mock).
        body: Raw request body string (empty string for GET).
        timestamp: Unix timestamp as string.
        client_id: Identifier of the calling service.

    Returns:
        Hex-encoded HMAC-SHA256 signature.
    """
    canonical = _build_canonical_string(method, path, timestamp, client_id, body)
    return hmac.new(
        secret.encode("utf-8"),
        canonical.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def verify_signature(
    secret: str,
    method: str,
    path: str,
    body: str,
    timestamp: str,
    client_id: str,
    signature: str,
) -> Tuple[bool, str]:
    """Verify an incoming HMAC-SHA256 signature with timestamp validation.

    Args:
        secret: Shared HMAC secret.
        method: HTTP method.
        path: Request path.
        body: Raw request body string.
        timestamp: Unix timestamp string from request header.
        client_id: Client identifier from request header.
        signature: Hex signature from request header.

    Returns:
        Tuple of (is_valid, error_message). error_message is empty on success.
    """
    # Validate timestamp freshness
    try:
        ts = float(timestamp)
    except (ValueError, TypeError):
        return False, "Invalid timestamp format"

    now = time.time()
    if abs(now - ts) > TIMESTAMP_TOLERANCE_SECONDS:
        return False, "Request timestamp expired"

    # Compute expected signature and compare in constant-time
    expected = generate_signature(secret, method, path, body, timestamp, client_id)
    if not hmac.compare_digest(expected, signature):
        return False, "Invalid HMAC signature"

    return True, ""
