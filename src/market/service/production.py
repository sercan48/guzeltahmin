"""PHASE-LIVE L6 — Production deployment profile for real-feed dry-run.

Pre-wired RuntimeConfig for running the full stack against real Pinnacle and
Betfair providers with publishing held in dry-run mode.

Additive: builds on L3 RuntimeConfig. No changes to M1-M11 / L1-L5.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .config import ProviderConfig, RuntimeConfig, SchedulerConfig, TelegramConfig


# ---------------------------------------------------------------------------
# Alert thresholds (operational, tunable)
# ---------------------------------------------------------------------------

@dataclass
class AlertThresholds:
    """Thresholds that trigger operational alerts."""
    degraded_mode: bool = True                  # alert whenever degraded
    provider_failure_rate: float = 0.25         # >25% provider failures → alert
    min_completeness: float = 0.80              # completeness < 80% → alert
    max_consecutive_empty_iterations: int = 120 # no jobs for N iters → alert
    replay_chain_must_be_valid: bool = True     # any chain failure → alert
    min_provider_availability: float = 0.90     # availability < 90% → alert


# ---------------------------------------------------------------------------
# Production profile
# ---------------------------------------------------------------------------

@dataclass
class ProductionProfile:
    """Full real-feed dry-run deployment profile.

    Wraps a RuntimeConfig plus deployment-level operational settings.
    dry_run is forced True — production publishing is never enabled here.
    """
    enable_pinnacle: bool = True
    enable_betfair: bool = True
    enable_betfair_outcome: bool = True
    poll_interval_seconds: float = 30.0
    scheduler_db: str = "prod_scheduler.db"
    truth_db: str = "prod_truth.db"
    control_db: str = "prod_control.db"
    bridge_db: str = "prod_bridge.db"
    report_dir: str = "prod_reports"
    log_level: str = "INFO"
    degraded_failure_threshold: int = 5
    vip_tier_threshold: str = "TIER_A"
    alerts: AlertThresholds = field(default_factory=AlertThresholds)

    # ------------------------------------------------------------------ #

    def provider_configs(self) -> List[ProviderConfig]:
        """Build the enabled provider list (real-feed rate limits)."""
        out: List[ProviderConfig] = []
        if self.enable_pinnacle:
            out.append(ProviderConfig(
                name="pinnacle", enabled=True,
                rate_capacity=1.0, rate_refill_per_sec=0.5,
                api_key_secret="PINNACLE_API_KEY",
            ))
        if self.enable_betfair:
            out.append(ProviderConfig(
                name="betfair", enabled=True,
                rate_capacity=1.0, rate_refill_per_sec=0.5,
                session_secret="BETFAIR_SESSION_TOKEN",
                app_key_secret="BETFAIR_APP_KEY",
            ))
        if self.enable_betfair_outcome:
            out.append(ProviderConfig(
                name="betfair_outcome", enabled=True,
                rate_capacity=1.0, rate_refill_per_sec=0.5,
                session_secret="BETFAIR_SESSION_TOKEN",
                app_key_secret="BETFAIR_APP_KEY",
            ))
        return out

    def to_runtime_config(self) -> RuntimeConfig:
        """Produce the RuntimeConfig consumed by build_runtime().

        dry_run is forced True regardless of any other setting.
        """
        cfg = RuntimeConfig(
            providers=self.provider_configs(),
            scheduler=SchedulerConfig(
                db_path=self.scheduler_db,
                poll_interval_seconds=self.poll_interval_seconds,
            ),
            telegram=TelegramConfig(
                dry_run=True,                       # ALWAYS dry-run in L6
                vip_tier_threshold=self.vip_tier_threshold,
            ),
            truth_db_path=self.truth_db,
            control_db_path=self.control_db,
            bridge_db_path=self.bridge_db,
            log_level=self.log_level,
            degraded_failure_threshold=self.degraded_failure_threshold,
        )
        return cfg

    def required_secrets(self) -> List[str]:
        """Secret names that must be present for the enabled providers."""
        names: List[str] = []
        if self.enable_pinnacle:
            names.append("PINNACLE_API_KEY")
        if self.enable_betfair or self.enable_betfair_outcome:
            names.extend(["BETFAIR_APP_KEY", "BETFAIR_SESSION_TOKEN"])
        # de-dup, preserve order
        seen: set = set()
        out: List[str] = []
        for n in names:
            if n not in seen:
                seen.add(n)
                out.append(n)
        return out

    def validate(self) -> None:
        """Validate the profile and its derived RuntimeConfig."""
        if self.poll_interval_seconds <= 0:
            raise ValueError("poll_interval_seconds must be > 0")
        if not (self.enable_pinnacle or self.enable_betfair
                or self.enable_betfair_outcome):
            raise ValueError("at least one provider must be enabled")
        self.to_runtime_config().validate()
