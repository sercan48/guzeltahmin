"""Task 2 — Odds Drift Engine.

Pure transformation of a single MarketKey time-series into drift signals.

Mathematical definitions
-------------------------
Let a selection's ordered series be points (t_i, o_i), i = 0..n-1, with o_i the
decimal odds at time t_i (hours). Define the *current* reference o_now as the
last pre-close observation (the latest horizon available), and o_open the
opening odds.

Fractional window changes (odds space):
    odds_change_24h = (o_now - o_24h) / o_24h
    odds_change_6h  = (o_now - o_6h)  / o_6h
    odds_change_1h  = (o_now - o_1h)  / o_1h
    total_drift     = (o_now - o_open) / o_open        # cumulative since open

When a horizon bucket is missing the corresponding change is None (never
back-filled — that would leak).

Velocity / acceleration (finite differences on the ordered series):
    v_i = (o_i - o_{i-1}) / (t_i - t_{i-1})            # odds units / hour
    a_i = (v_i - v_{i-1}) / (t_i - t_{i-1})            # odds units / hour^2

    drift_velocity     = v_{n-1}  (most recent instantaneous rate)
    drift_acceleration = a_{n-1}  (most recent change in rate)

A probability-space mirror is also reported because implied probability moves
roughly additively and is sign-stable across favourites/underdogs:
    p_i = 1 / o_i
    prob_drift_total = p_now - p_open                  # +ve => line shortened
    prob_velocity    = (p_{n-1} - p_{n-2}) / dt

Sign convention: in odds space a *negative* total_drift means the price
shortened (odds fell) — backing pressure on this selection. In prob space a
*positive* prob_drift_total means the same thing. Both are reported so callers
need not re-derive the sign.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Dict, List, Optional

from .schema import Horizon
from .timeseries import MarketTimeSeries


@dataclass
class DriftSignals:
    key_str: str
    odds_change_24h: Optional[float]
    odds_change_6h: Optional[float]
    odds_change_1h: Optional[float]
    total_drift: Optional[float]
    drift_velocity: Optional[float]          # odds units / hour (recent)
    drift_acceleration: Optional[float]      # odds units / hour^2 (recent)
    prob_drift_total: Optional[float]        # +ve => shortened
    prob_velocity: Optional[float]
    n_points: int
    direction: str                           # SHORTENING | DRIFTING | FLAT | NA

    def to_dict(self) -> dict:
        return asdict(self)


class OddsDriftEngine:
    """Computes drift signals for time-series. Stateless / pure."""

    FLAT_EPS = 1e-4  # |total_drift| below this is considered FLAT

    def compute(self, series: MarketTimeSeries) -> DriftSignals:
        o_open = series.odds_at(Horizon.OPENING)
        o_24 = series.odds_at(Horizon.H24)
        o_6 = series.odds_at(Horizon.H6)
        o_1 = series.odds_at(Horizon.H1)

        # o_now = latest pre-close reference: prefer 1h, else 6h, else 12h,
        # else 24h, else opening (whatever most-recent pre-match line exists).
        o_now = self._first_available(
            series,
            [Horizon.H1, Horizon.H6, Horizon.H12, Horizon.H24, Horizon.OPENING],
        )

        odds_change_24h = self._frac(o_now, o_24)
        odds_change_6h = self._frac(o_now, o_6)
        odds_change_1h = self._frac(o_now, o_1)
        total_drift = self._frac(o_now, o_open)

        vel, acc = self._velocity_acceleration([r.odds for r in series.ordered],
                                                [r.timestamp for r in series.ordered])
        p_vel, _ = self._velocity_acceleration(
            [1.0 / r.odds for r in series.ordered if r.odds > 1.0],
            [r.timestamp for r in series.ordered if r.odds > 1.0],
        )
        prob_total = None
        if o_open and o_now and o_open > 1.0 and o_now > 1.0:
            prob_total = (1.0 / o_now) - (1.0 / o_open)

        return DriftSignals(
            key_str=self._key_str(series),
            odds_change_24h=odds_change_24h,
            odds_change_6h=odds_change_6h,
            odds_change_1h=odds_change_1h,
            total_drift=total_drift,
            drift_velocity=vel,
            drift_acceleration=acc,
            prob_drift_total=prob_total,
            prob_velocity=p_vel,
            n_points=len(series.ordered),
            direction=self._direction(total_drift),
        )

    def compute_all(self, series_map) -> Dict[str, DriftSignals]:
        return {self._key_str(s): self.compute(s) for s in series_map.values()}

    # -- helpers ------------------------------------------------------------
    @staticmethod
    def _first_available(series: MarketTimeSeries, order) -> Optional[float]:
        for h in order:
            v = series.odds_at(h)
            if v is not None:
                return v
        return None

    @staticmethod
    def _frac(now: Optional[float], ref: Optional[float]) -> Optional[float]:
        if now is None or ref is None or ref == 0:
            return None
        return (now - ref) / ref

    @staticmethod
    def _velocity_acceleration(values: List[float], times):
        """Most-recent instantaneous velocity & acceleration via finite diffs."""
        if len(values) < 2:
            return None, None
        # hours between consecutive points
        def dt(a, b):
            return (b - a).total_seconds() / 3600.0

        velocities = []
        for i in range(1, len(values)):
            span = dt(times[i - 1], times[i])
            if span <= 0:
                continue
            velocities.append(((values[i] - values[i - 1]) / span, times[i], span))
        if not velocities:
            return None, None
        drift_velocity = velocities[-1][0]
        if len(velocities) < 2:
            return drift_velocity, None
        span = (velocities[-1][1] - velocities[-2][1]).total_seconds() / 3600.0
        if span <= 0:
            return drift_velocity, None
        drift_acceleration = (velocities[-1][0] - velocities[-2][0]) / span
        return drift_velocity, drift_acceleration

    def _direction(self, total_drift: Optional[float]) -> str:
        if total_drift is None:
            return "NA"
        if abs(total_drift) < self.FLAT_EPS:
            return "FLAT"
        # odds fell => shortened (backing pressure); odds rose => drifted (laying)
        return "SHORTENING" if total_drift < 0 else "DRIFTING"

    @staticmethod
    def _key_str(series: MarketTimeSeries) -> str:
        k = series.key
        bk = f"@{k.bookmaker}" if k.bookmaker else "@consensus"
        return f"{k.match_id}|{k.market}|{k.selection}{bk}"
