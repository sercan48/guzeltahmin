import time
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional, List
import jwt
import bcrypt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.core.config import settings
from app.db.session import get_db
from app.models.admin import Admin

try:
    import redis.asyncio as aioredis
except ImportError:
    aioredis = None  # type: ignore

logger = logging.getLogger(__name__)

# HTTP Bearer Auth scheme
security_scheme = HTTPBearer()

# Lazy-loaded Redis client for token blacklist
_redis_client: Optional["aioredis.Redis"] = None  # type: ignore


async def _get_redis() -> Optional["aioredis.Redis"]:  # type: ignore
    global _redis_client
    if aioredis is None:
        return None
    if _redis_client is not None:
        return _redis_client
    if not settings.REDIS_URL:
        return None
    try:
        _redis_client = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
        await _redis_client.ping()
        return _redis_client
    except Exception as exc:
        logger.warning("Redis unavailable for JWT blacklist: %s", exc)
        _redis_client = None
        return None


# --- PASSWORD HASHING ---

def hash_password(password: str) -> str:
    """Hash password using bcrypt."""
    salt = bcrypt.gensalt()
    return bcrypt.hashpw(password.encode("utf-8"), salt).decode("utf-8")


def verify_password(password: str, hashed: str) -> bool:
    """Verify password against bcrypt hash."""
    try:
        return bcrypt.checkpw(password.encode("utf-8"), hashed.encode("utf-8"))
    except Exception:
        return False


# --- JWT OPERATIONS ---

def create_access_token(admin_id: int, role: str) -> str:
    """Create 15-minute access token."""
    payload = {
        "sub": str(admin_id),
        "role": role,
        "type": "access",
        "exp": datetime.now(timezone.utc) + timedelta(minutes=15),
        "iat": datetime.now(timezone.utc),
    }
    return jwt.encode(payload, settings.JWT_SECRET, algorithm="HS256")


def create_refresh_token(admin_id: int, role: str) -> str:
    """Create 7-day refresh token."""
    payload = {
        "sub": str(admin_id),
        "role": role,
        "type": "refresh",
        "exp": datetime.now(timezone.utc) + timedelta(days=7),
        "iat": datetime.now(timezone.utc),
    }
    return jwt.encode(payload, settings.JWT_SECRET, algorithm="HS256")


def decode_token(token: str) -> dict:
    """Decode JWT token, raising appropriate HTTPException if invalid or expired."""
    try:
        payload = jwt.decode(token, settings.JWT_SECRET, algorithms=["HS256"])
        return payload
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has expired",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except jwt.InvalidTokenError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication token",
            headers={"WWW-Authenticate": "Bearer"},
        )


# --- REDIS REVOCATION & BLACKLISTING ---

async def blacklist_token(token: str, exp_seconds: int) -> None:
    """Store blacklisted token in Redis."""
    redis = await _get_redis()
    if redis is None:
        logger.warning("Token blacklisting skipped - Redis unavailable")
        return
    try:
        # Generate token hash to save Redis memory space
        import hashlib
        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        await redis.set(f"bl:{token_hash}", "1", ex=max(1, exp_seconds))
    except Exception as exc:
        logger.error("Failed to blacklist token: %s", exc)


async def is_token_blacklisted(token: str) -> bool:
    """Check if token is blacklisted in Redis."""
    redis = await _get_redis()
    if redis is None:
        return False
    try:
        import hashlib
        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        is_bl = await redis.get(f"bl:{token_hash}")
        return is_bl is not None
    except Exception as exc:
        logger.error("Failed to check blacklist in Redis: %s", exc)
        return False


# --- FASTAPI DEPENDENCIES ---

async def get_current_admin(
    credentials: HTTPAuthorizationCredentials = Depends(security_scheme),
    db: AsyncSession = Depends(get_db),
) -> Admin:
    """Verify bearer token, enforce whitelist/blacklist, and return Admin."""
    token = credentials.credentials
    if await is_token_blacklisted(token):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has been revoked/logged out",
            headers={"WWW-Authenticate": "Bearer"},
        )

    payload = decode_token(token)
    if payload.get("type") != "access":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token type, expected access token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    admin_id_str = payload.get("sub")
    if not admin_id_str:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token subject missing",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        admin_id = int(admin_id_str)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid admin ID in token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Fetch admin
    result = await db.execute(select(Admin).filter(Admin.id == admin_id, Admin.is_active == True))
    admin = result.scalars().first()
    if not admin:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Admin user not found or inactive",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return admin


class RoleChecker:
    """RBAC role checking helper."""

    def __init__(self, allowed_roles: List[str]) -> None:
        self.allowed_roles = allowed_roles

    def __call__(self, current_admin: Admin = Depends(get_current_admin)) -> Admin:
        if current_admin.role not in self.allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Operation forbidden: requires one of the roles: {', '.join(self.allowed_roles)}",
            )
        return current_admin


# Pre-defined dependencies
require_admin = RoleChecker(["admin"])
require_support = RoleChecker(["admin", "support"])
require_finance = RoleChecker(["admin", "finance"])
require_any_role = RoleChecker(["admin", "support", "finance"])
