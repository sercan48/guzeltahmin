"""Hybrid data source configuration — single switch between FREE and PAID tiers."""

from enum import Enum
from dataclasses import dataclass, field


class DataSourceTier(Enum):
    FREE = "free"
    PAID = "paid"


class SourceStatus(Enum):
    ACTIVE = "active"
    DEGRADED = "degraded"
    DOWN = "down"
    RATE_LIMITED = "rate_limited"


@dataclass
class SourceConfig:
    name: str
    enabled: bool
    priority: int  # lower = higher priority
    rate_limit_per_min: int
    daily_limit: int = 0  # 0 = unlimited
    monthly_limit: int = 0
    cost_per_month: float = 0.0
    requires_key: bool = False
    key_env_var: str = ""


@dataclass
class TierConfig:
    tier: DataSourceTier
    sources: dict[str, SourceConfig] = field(default_factory=dict)

    @property
    def total_monthly_cost(self) -> float:
        return sum(s.cost_per_month for s in self.sources.values() if s.enabled)


# ─── FREE TIER ───────────────────────────────────────────────

FREE_TIER = TierConfig(
    tier=DataSourceTier.FREE,
    sources={
        "football_data_uk": SourceConfig(
            name="Football-Data.co.uk",
            enabled=True,
            priority=1,
            rate_limit_per_min=60,
            cost_per_month=0,
        ),
        "football_data_org": SourceConfig(
            name="football-data.org API",
            enabled=True,
            priority=2,
            rate_limit_per_min=10,
            cost_per_month=0,
            requires_key=True,
            key_env_var="FOOTBALL_DATA_ORG_KEY",
        ),
        "openfootball": SourceConfig(
            name="Openfootball (GitHub)",
            enabled=True,
            priority=3,
            rate_limit_per_min=30,
            cost_per_month=0,
        ),
        "understat": SourceConfig(
            name="Understat (xG scrape)",
            enabled=True,
            priority=1,
            rate_limit_per_min=5,
            cost_per_month=0,
        ),
        "api_football": SourceConfig(
            name="API-Football Free",
            enabled=True,
            priority=4,
            rate_limit_per_min=1,
            daily_limit=100,
            cost_per_month=0,
            requires_key=True,
            key_env_var="API_FOOTBALL_KEY",
        ),
        "odds_api": SourceConfig(
            name="The Odds API Free",
            enabled=True,
            priority=2,
            rate_limit_per_min=10,
            monthly_limit=500,
            cost_per_month=0,
            requires_key=True,
            key_env_var="ODDS_API_KEY",
        ),
        "fifa_kaggle": SourceConfig(
            name="FIFA Kaggle Dataset",
            enabled=True,
            priority=1,
            rate_limit_per_min=999,
            cost_per_month=0,
        ),
    },
)

# ─── PAID TIER ───────────────────────────────────────────────

PAID_TIER = TierConfig(
    tier=DataSourceTier.PAID,
    sources={
        "football_data_uk": SourceConfig(
            name="Football-Data.co.uk",
            enabled=True,
            priority=1,
            rate_limit_per_min=60,
            cost_per_month=0,
        ),
        "football_data_org": SourceConfig(
            name="football-data.org Paid",
            enabled=True,
            priority=2,
            rate_limit_per_min=30,
            cost_per_month=12,
            requires_key=True,
            key_env_var="FOOTBALL_DATA_ORG_KEY",
        ),
        "openfootball": SourceConfig(
            name="Openfootball (GitHub)",
            enabled=True,
            priority=3,
            rate_limit_per_min=30,
            cost_per_month=0,
        ),
        "understat": SourceConfig(
            name="Understat (xG scrape)",
            enabled=True,
            priority=1,
            rate_limit_per_min=10,
            cost_per_month=0,
        ),
        "api_football": SourceConfig(
            name="API-Football Pro",
            enabled=True,
            priority=1,
            rate_limit_per_min=30,
            daily_limit=7500,
            cost_per_month=19,
            requires_key=True,
            key_env_var="API_FOOTBALL_KEY",
        ),
        "odds_api": SourceConfig(
            name="The Odds API Starter",
            enabled=True,
            priority=1,
            rate_limit_per_min=30,
            monthly_limit=20000,
            cost_per_month=30,
            requires_key=True,
            key_env_var="ODDS_API_KEY",
        ),
        "fifa_kaggle": SourceConfig(
            name="FIFA Kaggle Dataset",
            enabled=True,
            priority=1,
            rate_limit_per_min=999,
            cost_per_month=0,
        ),
    },
)


# ─── FALLBACK CHAINS ────────────────────────────────────────
# For each data type, define the priority order of sources to try

FALLBACK_CHAINS: dict[str, list[str]] = {
    "fixtures": ["api_football", "football_data_org", "football_data_uk"],
    "results": ["football_data_uk", "openfootball", "football_data_org"],
    "odds": ["odds_api", "football_data_uk"],
    "xg": ["understat", "football_data_uk"],  # implied xG fallback
    "injuries": ["api_football"],
    "standings": ["football_data_org", "football_data_uk"],
    "players": ["fifa_kaggle"],
    "squad_values": ["football_data_uk"],  # from transfermarkt scrape
}


# ─── ACTIVE CONFIG ───────────────────────────────────────────
# Change this single line to switch tiers

ACTIVE_TIER: DataSourceTier = DataSourceTier.FREE


def get_active_config() -> TierConfig:
    return FREE_TIER if ACTIVE_TIER == DataSourceTier.FREE else PAID_TIER


def get_source(name: str) -> SourceConfig:
    config = get_active_config()
    if name not in config.sources:
        raise KeyError(f"Unknown source: {name}")
    return config.sources[name]


def get_fallback_chain(data_type: str) -> list[str]:
    if data_type not in FALLBACK_CHAINS:
        raise KeyError(f"Unknown data type: {data_type}")
    config = get_active_config()
    return [s for s in FALLBACK_CHAINS[data_type] if s in config.sources and config.sources[s].enabled]


def print_cost_summary():
    """Print monthly cost breakdown for active tier."""
    config = get_active_config()
    print(f"\n{'='*50}")
    print(f"  Data Source Cost — {config.tier.value.upper()} TIER")
    print(f"{'='*50}")
    for name, src in config.sources.items():
        status = "✅" if src.enabled else "❌"
        cost = f"${src.cost_per_month:.0f}/ay" if src.cost_per_month > 0 else "Ücretsiz"
        print(f"  {status} {src.name}: {cost}")
    print(f"\n  💰 Toplam: ${config.total_monthly_cost:.0f}/ay")
    print(f"{'='*50}")
