"""Hybrid data source orchestrator — fallback chains, caching, and health tracking."""

import time
import json
import logging
from pathlib import Path
from datetime import datetime, timedelta
from typing import Any

from config.data_sources import (
    get_active_config, get_fallback_chain, get_source,
    SourceStatus, DataSourceTier,
)
from config.settings import CACHE_DIR

logger = logging.getLogger(__name__)


class RateLimiter:
    """Token-bucket rate limiter per source."""

    def __init__(self):
        self._last_call: dict[str, float] = {}
        self._daily_counts: dict[str, dict] = {}

    def wait_if_needed(self, source_name: str) -> None:
        source = get_source(source_name)
        min_interval = 60.0 / max(source.rate_limit_per_min, 1)

        last = self._last_call.get(source_name, 0)
        elapsed = time.time() - last
        if elapsed < min_interval:
            time.sleep(min_interval - elapsed)

        self._last_call[source_name] = time.time()

    def check_daily_limit(self, source_name: str) -> bool:
        source = get_source(source_name)
        if source.daily_limit == 0:
            return True

        today = datetime.now().strftime("%Y-%m-%d")
        if source_name not in self._daily_counts:
            self._daily_counts[source_name] = {"date": today, "count": 0}

        entry = self._daily_counts[source_name]
        if entry["date"] != today:
            entry["date"] = today
            entry["count"] = 0

        return entry["count"] < source.daily_limit

    def increment(self, source_name: str) -> None:
        today = datetime.now().strftime("%Y-%m-%d")
        if source_name not in self._daily_counts:
            self._daily_counts[source_name] = {"date": today, "count": 0}
        self._daily_counts[source_name]["count"] += 1


class SourceHealthTracker:
    """Track reliability of each data source."""

    def __init__(self):
        self._stats: dict[str, dict] = {}

    def record(self, source: str, success: bool, response_time_ms: int = 0):
        if source not in self._stats:
            self._stats[source] = {"success": 0, "fail": 0, "total_ms": 0}
        s = self._stats[source]
        if success:
            s["success"] += 1
        else:
            s["fail"] += 1
        s["total_ms"] += response_time_ms

    def get_status(self, source: str) -> SourceStatus:
        if source not in self._stats:
            return SourceStatus.ACTIVE
        s = self._stats[source]
        total = s["success"] + s["fail"]
        if total == 0:
            return SourceStatus.ACTIVE
        ratio = s["success"] / total
        if ratio >= 0.9:
            return SourceStatus.ACTIVE
        elif ratio >= 0.5:
            return SourceStatus.DEGRADED
        return SourceStatus.DOWN

    def get_report(self) -> dict:
        report = {}
        for src, s in self._stats.items():
            total = s["success"] + s["fail"]
            report[src] = {
                "status": self.get_status(src).value,
                "success_rate": round(s["success"] / max(total, 1), 3),
                "avg_response_ms": round(s["total_ms"] / max(total, 1)),
                "total_calls": total,
            }
        return report


class DataSourceManager:
    """Central orchestrator for all data sources with fallback chains."""

    def __init__(self):
        self.rate_limiter = RateLimiter()
        self.health = SourceHealthTracker()
        self._cache_dir = CACHE_DIR / "sources"
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        self._clients: dict[str, Any] = {}

    def _get_client(self, source_name: str):
        if source_name in self._clients:
            return self._clients[source_name]

        client = None
        if source_name == "api_football":
            from src.ingestion.api_football_client import APIFootballClient
            client = APIFootballClient()
        elif source_name == "football_data_org":
            from src.ingestion.football_data_org_client import FootballDataOrgClient
            client = FootballDataOrgClient()
        elif source_name == "football_data_uk":
            from src.ingestion.csv_loader import CSVLoader
            client = CSVLoader()
        elif source_name == "understat":
            from src.ingestion.understat_client import UnderstatClient
            client = UnderstatClient()
        elif source_name == "odds_api":
            from src.ingestion.odds_api_client import OddsAPIClient
            client = OddsAPIClient()
        elif source_name == "openfootball":
            from src.ingestion.openfootball_loader import OpenfootballLoader
            client = OpenfootballLoader()

        if client:
            self._clients[source_name] = client
        return client

    def fetch_with_fallback(self, data_type: str, **kwargs) -> Any:
        """Try each source in the fallback chain until one succeeds."""
        chain = get_fallback_chain(data_type)
        errors = []

        for source_name in chain:
            if not self.rate_limiter.check_daily_limit(source_name):
                logger.warning(f"[{source_name}] Daily limit reached, skipping")
                continue

            if self.health.get_status(source_name) == SourceStatus.DOWN:
                logger.warning(f"[{source_name}] Source is DOWN, skipping")
                continue

            try:
                self.rate_limiter.wait_if_needed(source_name)
                client = self._get_client(source_name)
                if client is None:
                    continue

                start = time.time()
                result = self._dispatch(client, source_name, data_type, **kwargs)
                elapsed_ms = int((time.time() - start) * 1000)

                self.rate_limiter.increment(source_name)
                self.health.record(source_name, True, elapsed_ms)
                logger.info(f"[{source_name}] {data_type} OK ({elapsed_ms}ms)")
                return result

            except Exception as e:
                self.health.record(source_name, False)
                errors.append(f"{source_name}: {e}")
                logger.warning(f"[{source_name}] Failed: {e}")
                continue

        logger.error(f"All sources failed for {data_type}: {errors}")
        return None

    def _dispatch(self, client, source_name: str, data_type: str, **kwargs) -> Any:
        """Route data_type to the appropriate client method."""
        method_map = {
            "fixtures": "get_fixtures",
            "results": "get_results",
            "odds": "get_odds",
            "xg": "get_xg",
            "injuries": "get_injuries",
            "standings": "get_standings",
        }
        method_name = method_map.get(data_type, f"get_{data_type}")
        method = getattr(client, method_name, None)
        if method is None:
            raise AttributeError(f"{source_name} has no method {method_name}")
        return method(**kwargs)

    def get_cached_or_fetch(
        self, data_type: str, cache_key: str, ttl_hours: int = 6, **kwargs
    ) -> Any:
        """Cache-first strategy: return cached data if fresh, else fetch."""
        cache_file = self._cache_dir / f"{cache_key}.json"

        if cache_file.exists():
            mtime = datetime.fromtimestamp(cache_file.stat().st_mtime)
            if datetime.now() - mtime < timedelta(hours=ttl_hours):
                with open(cache_file, "r", encoding="utf-8") as f:
                    return json.load(f)

        result = self.fetch_with_fallback(data_type, **kwargs)
        if result is not None:
            try:
                with open(cache_file, "w", encoding="utf-8") as f:
                    json.dump(result, f, ensure_ascii=False, default=str)
            except (TypeError, ValueError):
                pass

        return result

    def print_health_report(self):
        report = self.health.get_report()
        config = get_active_config()
        print(f"\n{'='*55}")
        print(f"  Veri Kaynağı Sağlık Raporu — {config.tier.value.upper()}")
        print(f"{'='*55}")
        for src, info in report.items():
            icon = {"active": "🟢", "degraded": "🟡", "down": "🔴"}.get(info["status"], "⚪")
            print(f"  {icon} {src}: %{info['success_rate']*100:.0f} başarı, "
                  f"~{info['avg_response_ms']}ms, {info['total_calls']} çağrı")
        print(f"{'='*55}")
