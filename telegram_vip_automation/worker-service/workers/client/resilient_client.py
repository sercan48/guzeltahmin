"""Resilient API client for worker-service — drop-in swap for WorkerApiClient.

Uses ResilientHttpClient for automatic retry with exponential backoff and
circuit breaker protection, combined with HMAC-SHA256 request signing.
"""

import logging
import time
import json
from typing import Any, Optional

from workers.config import settings
from shared.http_client import CircuitBreakerOpen, ResilientHttpClient
from shared.security import generate_signature

logger = logging.getLogger(__name__)


class ResilientWorkerClient:
    """Worker-side API client with retry, circuit breaker, and HMAC signing."""

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
    ) -> Optional[Any]:
        # Generate HMAC-SHA256 signing headers
        timestamp = str(int(time.time()))
        client_id = "worker-service"

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

        try:
            return await self._http.request(
                method, path, json_data=json_data, headers=req_headers
            )
        except CircuitBreakerOpen:
            logger.error(
                "worker_api_circuit_open",
                extra={"method": method, "path": path},
            )
            return None

    async def get_expired_subscriptions(self) -> list[dict[str, Any]]:
        res = await self._safe_request("GET", "/api/v1/subscriptions/expired")
        return res if isinstance(res, list) else []

    async def deactivate_subscription(self, subscription_id: int) -> bool:
        res = await self._safe_request(
            "POST", f"/api/v1/subscriptions/deactivate/{subscription_id}"
        )
        return res is not None and res.get("status") == "success"

    async def get_pending_campaigns(self) -> list[dict[str, Any]]:
        res = await self._safe_request("GET", "/api/v1/bot/campaigns/pending")
        return res if isinstance(res, list) else []

    async def log_campaign(self, subscription_id: int, user_id: int, notification_type: str) -> bool:
        payload = {
            "subscription_id": subscription_id,
            "user_id": user_id,
            "notification_type": notification_type
        }
        res = await self._safe_request("POST", "/api/v1/bot/campaigns/log", json_data=payload)
        return res is not None and res.get("status") == "success"

    async def get_pending_campaign_executions(self) -> list[dict[str, Any]]:
        res = await self._safe_request("GET", "/api/v1/bot/campaign-executions/pending")
        return res if isinstance(res, list) else []

    async def complete_campaign_execution(self, execution_id: int, status: str) -> bool:
        payload = {"status": status}
        res = await self._safe_request("POST", f"/api/v1/bot/campaign-executions/{execution_id}/complete", json_data=payload)
        return res is not None and res.get("status") == "success"

    async def trigger_churn_risk_calculation(self) -> bool:
        res = await self._safe_request("POST", "/api/v1/analytics/calculate-risk")
        return res is not None and res.get("status") == "success"

    async def trigger_materialized_view_refresh(self) -> bool:
        res = await self._safe_request("POST", "/api/v1/analytics/refresh-views")
        return res is not None and res.get("status") == "success"

    async def trigger_reconciliation(self) -> Optional[dict[str, Any]]:
        res = await self._safe_request("POST", "/api/v1/reconciliation/run")
        return res



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
            "source_service": "worker"
        }
        return await self._safe_request("POST", "/api/v1/events", json_data=payload)


resilient_worker_client = ResilientWorkerClient()
