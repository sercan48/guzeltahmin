"""M7 — Shadow timeline simulator.

Deterministically generates the T-72h -> CLOSE event stream for a synthetic
match: snapshot ticks become ODDS_UPDATED triggers (multi-provider odds that
evolve), plus MATCH_CREATED / MATCH_STARTED. Supports a deterministic seed with
a stochastic noise overlay and drift injection (liquidity shock, sharp move,
API delay).

Simulation only — no prediction logic, no execution. It produces M5 ``Trigger``
objects that drive the existing orchestrator unchanged.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Dict, List, Optional, Tuple

from ..orchestration import Trigger, TriggerType


class DriftType(str, Enum):
    LIQUIDITY_SHOCK = "LIQUIDITY_SHOCK"   # vig widens, depth drops
    SHARP_MOVE = "SHARP_MOVE"             # sudden informed prob shift (steam)
    API_DELAY = "API_DELAY"               # a provider's snapshot is missing


@dataclass
class DriftInjection:
    type: DriftType
    at_tick: int                 # tick index the drift applies to
    magnitude: float = 0.0       # interpretation depends on type
    provider: Optional[str] = None   # API_DELAY: which provider drops


# tick schedule (hours before kickoff) -> snapshot_type label
DEFAULT_TICKS: List[Tuple[float, str]] = [
    (72.0, "OPEN"), (48.0, "OPEN"), (24.0, "T-24h"), (12.0, "T-12h"),
    (6.0, "T-6h"), (3.0, "T-1h"), (1.0, "T-1h"), (0.0, "CLOSE"),
]


@dataclass
class TimelineConfig:
    providers: List[str] = field(default_factory=lambda: ["pinnacle", "bet365", "williamhill"])
    ticks: List[Tuple[float, str]] = field(default_factory=lambda: list(DEFAULT_TICKS))
    home_prob_start: float = 0.45
    home_prob_end: float = 0.55          # baseline HOME shortening
    base_vig: float = 0.05               # overround = 1 + vig
    noise_std: float = 0.0               # stochastic overlay (0 => deterministic/paper)
    provider_bias: Dict[str, float] = field(default_factory=lambda: {
        "pinnacle": 0.0, "bet365": 0.004, "williamhill": -0.006,
    })


def _clip(x, lo, hi):
    return max(lo, min(hi, x))


def generate_timeline(
    match_id: str,
    kickoff: datetime,
    seed: int = 0,
    drifts: Optional[List[DriftInjection]] = None,
    config: TimelineConfig = TimelineConfig(),
) -> List[Trigger]:
    """Build the ordered trigger stream for one match."""
    if kickoff.tzinfo is None:
        kickoff = kickoff.replace(tzinfo=timezone.utc)
    drifts = drifts or []
    drift_at: Dict[int, List[DriftInjection]] = {}
    for d in drifts:
        drift_at.setdefault(d.at_tick, []).append(d)
    rng = random.Random(seed)

    triggers: List[Trigger] = []
    # MATCH_CREATED at the first tick
    create_ts = kickoff - timedelta(hours=config.ticks[0][0])
    triggers.append(Trigger(match_id, TriggerType.MATCH_CREATED,
                            f"{match_id}:created", create_ts,
                            {"kickoff": kickoff.isoformat()}))

    n = len(config.ticks)
    for i, (hours, stype) in enumerate(config.ticks):
        ts = kickoff - timedelta(hours=hours)
        # baseline HOME prob path (monotone shortening) + noise + sharp drift
        frac = i / (n - 1) if n > 1 else 1.0
        home_p = config.home_prob_start + (config.home_prob_end - config.home_prob_start) * frac
        vig = config.base_vig
        dropped: set = set()
        for d in drift_at.get(i, []):
            if d.type == DriftType.SHARP_MOVE:
                home_p += d.magnitude                  # informed jump
            elif d.type == DriftType.LIQUIDITY_SHOCK:
                vig += abs(d.magnitude)                 # vig widens
            elif d.type == DriftType.API_DELAY and d.provider:
                dropped.add(d.provider)
        if config.noise_std > 0:
            home_p += rng.gauss(0.0, config.noise_std)
        home_p = _clip(home_p, 0.10, 0.85)
        # split the remainder draw/away
        draw_p = (1.0 - home_p) * 0.45
        away_p = (1.0 - home_p) * 0.55
        true_probs = {"HOME": home_p, "DRAW": draw_p, "AWAY": away_p}

        snaps = []
        for prov in config.providers:
            if prov in dropped:
                continue                                # API delay: provider missing
            bias = config.provider_bias.get(prov, 0.0)
            for sel, p in true_probs.items():
                book_implied = _clip(p + (bias if sel == "HOME" else -bias / 2), 0.02, 0.97)
                book_implied *= (1.0 + vig)             # add overround
                odds = round(1.0 / book_implied, 4)
                snaps.append({"provider": prov, "market": "1X2", "selection": sel,
                              "odds": odds, "snapshot_type": stype})
        triggers.append(Trigger(match_id, TriggerType.ODDS_UPDATED,
                                f"{match_id}:tick{i}", ts, {"snapshots": snaps}))

    # kickoff
    triggers.append(Trigger(match_id, TriggerType.MATCH_STARTED,
                            f"{match_id}:started", kickoff, {}))
    return triggers
