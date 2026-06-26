"""
src/integrations/polymarket/client.py — Official Polymarket REST API Clients

Uses only official public endpoints. No authentication required for reads.
No scraping. No unofficial SDKs.

Official endpoints:
  Gamma API : https://gamma-api.polymarket.com  (market discovery, prices)
  CLOB API  : https://clob.polymarket.com        (order book, mid prices)

Ref: https://docs.polymarket.com/api-reference/introduction
"""
from __future__ import annotations

import logging
import time
from typing import Any, Generator

import requests

_GAMMA_BASE = "https://gamma-api.polymarket.com"
_CLOB_BASE = "https://clob.polymarket.com"
_DEFAULT_TIMEOUT = 15
_DEFAULT_RETRIES = 3
_RETRY_BACKOFF = 2.0
_COURTESY_DELAY = 0.4   # seconds between paginated requests

logger = logging.getLogger(__name__)


class _BaseClient:
    def __init__(self, base_url: str, timeout: int, retries: int) -> None:
        self._base = base_url
        self._timeout = timeout
        self._retries = retries
        self._session = requests.Session()
        self._session.headers.update({"User-Agent": "GuzeltahminObserver/1.0 (research)"})

    def _get(self, path: str, params: dict | None = None) -> Any:
        url = f"{self._base}{path}"
        last_exc: Exception | None = None
        for attempt in range(self._retries):
            try:
                resp = self._session.get(url, params=params, timeout=self._timeout)
                if resp.status_code == 429:
                    wait = _RETRY_BACKOFF ** (attempt + 1)
                    logger.warning("Rate limited by %s; waiting %.1fs", self._base, wait)
                    time.sleep(wait)
                    continue
                resp.raise_for_status()
                return resp.json()
            except requests.exceptions.RequestException as exc:
                last_exc = exc
                if attempt < self._retries - 1:
                    time.sleep(_RETRY_BACKOFF)
        logger.debug("Request failed after %d attempts: %s %s — %s",
                     self._retries, path, params, last_exc)
        return None


class GammaClient(_BaseClient):
    """
    Read-only Polymarket Gamma REST API client.
    Provides market discovery, prices, and event data.
    No authentication required.
    """

    def __init__(
        self,
        timeout: int = _DEFAULT_TIMEOUT,
        retries: int = _DEFAULT_RETRIES,
    ) -> None:
        super().__init__(_GAMMA_BASE, timeout, retries)

    def get_markets(
        self,
        active: bool = True,
        limit: int = 100,
        offset: int = 0,
        enable_order_book: bool = False,
    ) -> list[dict]:
        """Fetch one page of markets from the Gamma API."""
        params: dict[str, Any] = {
            "active": str(active).lower(),
            "limit": limit,
            "offset": offset,
        }
        if enable_order_book:
            params["enableOrderBook"] = "true"
        result = self._get("/markets", params)
        return result if isinstance(result, list) else []

    def get_market(self, market_id: str) -> dict | None:
        """Fetch a single market by ID."""
        return self._get(f"/markets/{market_id}")

    def get_events(
        self,
        active: bool = True,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict]:
        """Fetch one page of events from the Gamma API."""
        params: dict[str, Any] = {
            "active": str(active).lower(),
            "limit": limit,
            "offset": offset,
        }
        result = self._get("/events", params)
        return result if isinstance(result, list) else []

    def iter_all_markets(
        self,
        active: bool = True,
        page_size: int = 100,
    ) -> Generator[dict, None, None]:
        """Generator that paginates through all markets. Includes courtesy delays."""
        offset = 0
        while True:
            page = self.get_markets(active=active, limit=page_size, offset=offset)
            if not page:
                break
            yield from page
            if len(page) < page_size:
                break
            offset += page_size
            time.sleep(_COURTESY_DELAY)


class ClobClient(_BaseClient):
    """
    Read-only Polymarket CLOB REST API client.
    Provides order book data (bid/ask). No authentication for reads.
    """

    def __init__(
        self,
        timeout: int = _DEFAULT_TIMEOUT,
        retries: int = _DEFAULT_RETRIES,
    ) -> None:
        super().__init__(_CLOB_BASE, timeout, retries)

    def get_midpoint(self, token_id: str) -> float | None:
        """Get mid-price for a CLOB token. Returns float 0-1 or None."""
        result = self._get("/midpoint", {"token_id": token_id})
        if result and "mid" in result:
            try:
                return float(result["mid"])
            except (TypeError, ValueError):
                pass
        return None

    def get_order_book(self, token_id: str) -> dict | None:
        """Get full order book (bids/asks) for a CLOB token."""
        return self._get("/book", {"token_id": token_id})

    def get_best_bid_ask(self, token_id: str) -> tuple[float | None, float | None]:
        """
        Returns (best_bid, best_ask) for a token.
        Returns (None, None) on any failure.
        """
        book = self.get_order_book(token_id)
        if not book:
            return None, None
        try:
            bids = book.get("bids") or []
            asks = book.get("asks") or []
            best_bid = float(bids[0]["price"]) if bids else None
            best_ask = float(asks[0]["price"]) if asks else None
            return best_bid, best_ask
        except (KeyError, IndexError, TypeError, ValueError):
            return None, None
