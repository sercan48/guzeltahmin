"""Worker API Client module re-exporting the Resilient Worker Client."""

from workers.client.resilient_client import resilient_worker_client as worker_api_client

__all__ = ["worker_api_client"]
