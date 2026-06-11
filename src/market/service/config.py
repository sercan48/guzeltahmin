"""PHASE-LIVE L3 — Runtime service configuration.

Pure-dataclass, stdlib-only. No external deps. Validated on startup.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List


@dataclass
class ProviderConfig:
    """Single provider slot (Pinnacle or Betfair)."""
    name: str                              # "pinnacle" | "betfair" | "betfair_outcome"
    enabled: bool = True
    rate_capacity: float = 1.0
    rate_refill_per_sec: float = 0.5       # token-bucket: ~1 req/2s default
    retry_max: int = 3
    # secret names resolved from SecretProvider at runtime
    api_key_secret: str = "PINNACLE_API_KEY"
    session_secret: str = "BETFAIR_SESSION_TOKEN"
    app_key_secret: str = "BETFAIR_APP_KEY"
    base_url: str = ""                     # empty = library default


@dataclass
class TelegramConfig:
    bot_token_secret: str = "TELEGRAM_BOT_TOKEN"   # env var name
    vip_channel_id: str = ""              # "@vip" or "-100xxx"
    standard_channel_id: str = ""         # "@std" or "-100xxx"
    vip_tier_threshold: str = "TIER_A"    # signals >= this tier → VIP channel
    dry_run: bool = True                  # default: no real messages sent
    timeout: float = 10.0


@dataclass
class SchedulerConfig:
    db_path: str = "scheduler.db"
    poll_interval_seconds: float = 30.0   # sleep between loop iterations
    grace_seconds: float = 3600.0         # missed-snapshot grace window


@dataclass
class RuntimeConfig:
    providers: List[ProviderConfig] = field(default_factory=list)
    scheduler: SchedulerConfig = field(default_factory=SchedulerConfig)
    telegram: TelegramConfig = field(default_factory=TelegramConfig)
    truth_db_path: str = "truth.db"
    control_db_path: str = "control.db"
    bridge_db_path: str = "bridge.db"
    log_level: str = "INFO"
    lock_path: str = "/tmp/miw_service.lock"
    degraded_failure_threshold: int = 5   # consecutive ProviderErrors → degraded

    def validate(self) -> None:
        """Raise ValueError on invalid config. Call before opening connections."""
        if self.scheduler.poll_interval_seconds <= 0:
            raise ValueError("poll_interval_seconds must be > 0")
        if self.degraded_failure_threshold <= 0:
            raise ValueError("degraded_failure_threshold must be > 0")
        if not self.telegram.dry_run:
            if not self.telegram.vip_channel_id:
                raise ValueError("vip_channel_id required when dry_run=False")
            if not self.telegram.standard_channel_id:
                raise ValueError("standard_channel_id required when dry_run=False")
        if self.telegram.vip_tier_threshold not in ("REJECT", "TIER_C", "TIER_B", "TIER_A", "TIER_S"):
            raise ValueError(f"unknown vip_tier_threshold: {self.telegram.vip_tier_threshold!r}")
        for pc in self.providers:
            if pc.rate_capacity <= 0 or pc.rate_refill_per_sec <= 0:
                raise ValueError(f"provider {pc.name!r}: rate params must be > 0")
