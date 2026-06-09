"""Truth canonicalization engine (Phase-16 / F16 buildout, module M1).

Pure, deterministic, network-free. Two jobs:

1. Odds-format canonicalization: decimal / fractional / american -> decimal.
2. De-vig: convert a market's quoted decimal odds into fair probabilities that
   sum to 1, via three methods + an ensemble:
     - multiplicative  (proportional; baseline)
     - power           (corrects favorite-longshot bias; solve exponent)
     - shin            (insider-trade adjusted; solve z)
     - ensemble        (mean of the three, renormalized)

This is the single de-vig implementation the Truth Store (M2) and any downstream
consumer must use. It does NOT change R1.2's existing inline de-vig; it is a new,
shared, fully-tested primitive that later modules wire in.

References: MIW_SHARP_ANCHOR_CALIBRATION (F15 §2), MIW_SHARP_DATA_INFRASTRUCTURE
(F16 §2.1), MIW_TRUTH_WAREHOUSE_BOOTSTRAP (§4).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, Mapping, Tuple, Union


class OddsFormat(str, Enum):
    DECIMAL = "decimal"
    FRACTIONAL = "fractional"
    AMERICAN = "american"


class DevigMethod(str, Enum):
    MULTIPLICATIVE = "multiplicative"
    POWER = "power"
    SHIN = "shin"
    ENSEMBLE = "ensemble"


# ---------------------------------------------------------------------------
# 1. Odds-format canonicalization -> decimal
# ---------------------------------------------------------------------------
def to_decimal(value: Union[float, int, str, Tuple[int, int]],
               fmt: OddsFormat = OddsFormat.DECIMAL) -> float:
    """Convert any supported odds representation to decimal odds (> 1.0).

    decimal     : numeric, returned as float (must be > 1.0)
    fractional  : "a/b" string or (a, b) tuple   -> a/b + 1
    american    : signed int or numeric string   -> +m: m/100+1 ; -m: 100/|m|+1
    """
    if fmt == OddsFormat.DECIMAL:
        d = float(value)
        if not d > 1.0:
            raise ValueError(f"decimal odds must be > 1.0, got {d}")
        return d

    if fmt == OddsFormat.FRACTIONAL:
        if isinstance(value, tuple):
            a, b = value
        else:
            a_str, b_str = str(value).split("/")
            a, b = float(a_str), float(b_str)
        if b == 0:
            raise ValueError("fractional denominator cannot be 0")
        return a / b + 1.0

    if fmt == OddsFormat.AMERICAN:
        m = float(value)
        if m == 0:
            raise ValueError("american odds cannot be 0")
        return (m / 100.0 + 1.0) if m > 0 else (100.0 / abs(m) + 1.0)

    raise ValueError(f"unknown odds format: {fmt}")


# ---------------------------------------------------------------------------
# 2. De-vig
# ---------------------------------------------------------------------------
@dataclass
class DevigResult:
    fair_probs: Dict[str, float]      # sums to 1.0
    overround: float                  # R = sum(1/odds); > 1 with vig
    method: str
    components: Dict[str, Dict[str, float]] = field(default_factory=dict)  # per-method (ensemble)

    def as_odds(self) -> Dict[str, float]:
        return {s: (1.0 / p if p > 0 else float("inf")) for s, p in self.fair_probs.items()}


def _raw_implied(odds: Mapping[str, float]) -> Tuple[Dict[str, float], float]:
    q = {s: 1.0 / o for s, o in odds.items() if o and o > 1.0}
    return q, sum(q.values())


def devig_multiplicative(odds: Mapping[str, float]) -> DevigResult:
    q, R = _raw_implied(odds)
    if R <= 0:
        return DevigResult({}, 0.0, DevigMethod.MULTIPLICATIVE.value)
    return DevigResult({s: v / R for s, v in q.items()}, R, DevigMethod.MULTIPLICATIVE.value)


def devig_power(odds: Mapping[str, float], iters: int = 80) -> DevigResult:
    """Fair p_i = q_i^(1/k) with k solved so that sum(p_i) = 1.

    Since sum(q_i) = R > 1 and q_i in (0,1), the normalizing exponent satisfies
    1/k > 1 (k < 1). Bisection on k in (0, 1].
    """
    q, R = _raw_implied(odds)
    if R <= 0:
        return DevigResult({}, 0.0, DevigMethod.POWER.value)

    def total(k: float) -> float:
        e = 1.0 / k
        return sum(v ** e for v in q.values())

    # find k in (lo, hi] with total(k) = 1. total is increasing in k here.
    lo, hi = 1e-6, 1.0
    # total(1.0) = R > 1 ; total(lo) -> 0  (very large exponent on values<1)
    for _ in range(iters):
        mid = 0.5 * (lo + hi)
        if total(mid) > 1.0:
            hi = mid
        else:
            lo = mid
    k = 0.5 * (lo + hi)
    e = 1.0 / k
    p = {s: v ** e for s, v in q.items()}
    norm = sum(p.values()) or 1.0
    return DevigResult({s: v / norm for s, v in p.items()}, R, DevigMethod.POWER.value)


def devig_shin(odds: Mapping[str, float], iters: int = 80) -> DevigResult:
    """Shin (1992/93) de-vig: solve for insider-trade fraction z in [0, 1).

        p_i = ( sqrt( z^2 + 4(1-z) * q_i^2 / B ) - z ) / ( 2(1-z) ),   B = sum q_i
        find z s.t. sum_i p_i = 1.  sum is decreasing in z; bisection.
    """
    q, R = _raw_implied(odds)
    if R <= 0:
        return DevigResult({}, 0.0, DevigMethod.SHIN.value)
    B = R

    def p_of(z: float) -> Dict[str, float]:
        if z >= 1.0:
            z = 1.0 - 1e-9
        denom = 2.0 * (1.0 - z)
        out = {}
        for s, qi in q.items():
            root = math.sqrt(z * z + 4.0 * (1.0 - z) * qi * qi / B)
            out[s] = (root - z) / denom
        return out

    def total(z: float) -> float:
        return sum(p_of(z).values())

    # total(0) = sqrt(B) > 1 ; total(z) decreasing toward 0
    lo, hi = 0.0, 0.999
    if total(hi) > 1.0:
        # vig so large even max z can't reach 1; fall back to multiplicative
        return devig_multiplicative(odds)
    for _ in range(iters):
        mid = 0.5 * (lo + hi)
        if total(mid) > 1.0:
            lo = mid
        else:
            hi = mid
    z = 0.5 * (lo + hi)
    p = p_of(z)
    norm = sum(p.values()) or 1.0
    return DevigResult({s: v / norm for s, v in p.items()}, R, DevigMethod.SHIN.value)


def devig_ensemble(odds: Mapping[str, float]) -> DevigResult:
    """Mean of multiplicative/power/shin fair probs, renormalized to sum 1."""
    parts = {
        DevigMethod.MULTIPLICATIVE.value: devig_multiplicative(odds),
        DevigMethod.POWER.value: devig_power(odds),
        DevigMethod.SHIN.value: devig_shin(odds),
    }
    selections = list(odds.keys())
    avg = {}
    for s in selections:
        vals = [r.fair_probs.get(s, 0.0) for r in parts.values()]
        avg[s] = sum(vals) / len(vals)
    norm = sum(avg.values()) or 1.0
    fair = {s: v / norm for s, v in avg.items()}
    _, R = _raw_implied(odds)
    return DevigResult(
        fair_probs=fair,
        overround=R,
        method=DevigMethod.ENSEMBLE.value,
        components={m: r.fair_probs for m, r in parts.items()},
    )


_DISPATCH = {
    DevigMethod.MULTIPLICATIVE: devig_multiplicative,
    DevigMethod.POWER: devig_power,
    DevigMethod.SHIN: devig_shin,
    DevigMethod.ENSEMBLE: devig_ensemble,
}


def devig(odds: Mapping[str, float],
          method: DevigMethod = DevigMethod.ENSEMBLE) -> DevigResult:
    """De-vig a single market's quoted decimal odds into fair probabilities."""
    return _DISPATCH[method](odds)
