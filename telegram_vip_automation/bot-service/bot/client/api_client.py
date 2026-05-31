"""API Client module re-exporting the Resilient API Client."""

from bot.client.resilient_client import resilient_api_client as api_client

__all__ = ["api_client"]
