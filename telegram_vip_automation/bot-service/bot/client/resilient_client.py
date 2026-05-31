"""Resilient API client for bot-service — drop-in swap for ApiServiceClient.

Uses ResilientHttpClient for automatic retry with exponential backoff and
circuit breaker protection, combined with HMAC-SHA256 request signing.
"""

import logging
import time
import json
import uuid
from typing import Any, Optional

from bot.config import settings
from shared.http_client import CircuitBreakerOpen, ResilientHttpClient
from shared.security import generate_signature

logger = logging.getLogger(__name__)


class ResilientApiClient:
    """Bot-side API client with retry, circuit breaker, and HMAC signing."""

    def __init__(
        self,
        base_url: str = settings.API_SERVICE_URL,
    ) -> None:
        self._http = ResilientHttpClient(
            base_url=base_url,
            default_headers={
                "Content-Type": "application/json",
            },
        )

    async def _safe_request(
        self,
        method: str,
        path: str,
        json_data: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> Optional[Any]:
        # Generate HMAC-SHA256 signing headers
        timestamp = str(int(time.time()))
        client_id = "bot-service"

        body_str = ""
        if json_data is not None:
            body_str = json.dumps(json_data, separators=(",", ":"))

        sig = generate_signature(
            secret=settings.HMAC_SECRET,
            method=method,
            path=path,
            body=body_str,
            timestamp=timestamp,
            client_id=client_id,
        )

        req_headers = {
            "X-Signature": sig,
            "X-Timestamp": timestamp,
            "X-Client-ID": client_id,
        }
        if headers:
            req_headers.update(headers)

        try:
            return await self._http.request(
                method, path, json_data=json_data, headers=req_headers
            )
        except CircuitBreakerOpen:
            logger.error(
                "bot_api_circuit_open",
                extra={"method": method, "path": path},
            )
            return None

    async def get_packages(self) -> list[dict[str, Any]]:
        res = await self._safe_request("GET", "/api/v1/packages")
        return res if isinstance(res, list) else []

    async def create_payment(
        self,
        telegram_id: int,
        package_id: int,
        idempotency_key: Optional[str] = None,
        username: str = "",
        first_name: str = "",
        last_name: str = "",
    ) -> Optional[dict[str, Any]]:
        if not idempotency_key:
            idempotency_key = f"pay_{uuid.uuid4().hex}"
        payload = {
            "telegram_id": telegram_id,
            "package_id": package_id,
            "idempotency_key": idempotency_key,
            "username": username,
            "first_name": first_name,
            "last_name": last_name,
        }
        return await self._safe_request(
            "POST", "/api/v1/payments/mock", json_data=payload
        )

    async def get_subscription_status(
        self, telegram_id: int
    ) -> Optional[dict[str, Any]]:
        return await self._safe_request(
            "GET",
            "/api/v1/subscriptions/status",
            headers={"X-Telegram-Id": str(telegram_id)},
        )

    async def simulate_payment_webhook(
        self, provider_tx_id: str
    ) -> Optional[dict[str, Any]]:
        payload = {"provider_tx_id": provider_tx_id}
        # Webhook endpoint does not use HMAC signature validation; it uses X-Internal-Token legacy auth.
        headers = {"X-Internal-Token": settings.INTERNAL_API_TOKEN}
        return await self._safe_request(
            "POST", "/api/v1/payments/webhook/mock", json_data=payload, headers=headers
        )

    async def claim_free_trial(self, telegram_id: int) -> Optional[dict[str, Any]]:
        payload = {"telegram_id": telegram_id}
        return await self._safe_request("POST", "/api/v1/bot/trial/claim", json_data=payload)

    async def validate_coupon(self, code: str, telegram_id: int, payment_id: Optional[int] = None) -> Optional[dict[str, Any]]:
        payload = {"code": code, "telegram_id": telegram_id}
        if payment_id is not None:
            payload["payment_id"] = payment_id
        return await self._safe_request("POST", "/api/v1/bot/coupon/validate", json_data=payload)

    async def get_referral_code(self, telegram_id: int) -> Optional[dict[str, Any]]:
        return await self._safe_request(
            "GET", "/api/v1/bot/referral/code", headers={"X-Telegram-Id": str(telegram_id)}
        )

    async def log_referral_click(self, telegram_id: int, code: str) -> Optional[dict[str, Any]]:
        payload = {"telegram_id": telegram_id, "code": code}
        return await self._safe_request("POST", "/api/v1/bot/referral/click", json_data=payload)

    async def publish_event(
        self,
        event_type: str,
        user_id: Optional[int] = None,
        payload_json: Optional[dict[str, Any]] = None,
        correlation_id: Optional[str] = None
    ) -> Optional[dict[str, Any]]:
        import uuid
        payload = {
            "event_type": event_type,
            "user_id": user_id,
            "payload_json": payload_json or {},
            "correlation_id": correlation_id or f"corr_{uuid.uuid4().hex}",
            "source_service": "bot"
        }
        return await self._safe_request("POST", "/api/v1/events", json_data=payload)


resilient_api_client = ResilientApiClient()
