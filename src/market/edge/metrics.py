"""Task 2 — Edge Metrics (a progressive discount cascade).

Edge magnitude is measured as a cascade of progressively more conservative
estimates. Each stage multiplies the previous by an orthogonal factor in [0, 1]
(discount-only — confirming signals raise *confidence* in Task 3, not the edge
magnitude). This keeps edges conservative and every adjustment auditable.

Mathematical definitions
-------------------------
    raw_edge            = p_model / p_market - 1            (EV at fair market odds)

    calibrated_edge     = raw_edge * f_cal
        f_cal = calibration_quality in [0,1]               (shrink if model mis-calibrated)

    market_adjusted_edge = calibrated_edge * f_mkt
        f_mkt = 1 / mean_overround in (0,1]                (realizable fraction net of vig)

    drift_adjusted_edge = market_adjusted_edge * f_drift
        align = sign(raw_edge) * prob_drift_total
        contradiction = clip(max(0, -align)/D_REF, 0, 1)   (market moving against the model)
        stability     = clip(1 - |prob_velocity|/V_REF, floor, 1)
        f_drift = clip((1 - contradiction) * stability, 0, 1)

    sharp_adjusted_edge = drift_adjusted_edge * f_sharp     (headline edge)
        align_s = sign(raw_edge) * sharp_proxy
        f_sharp = 1 - clip(max(0, -align_s)/SH_REF, 0, 1)  (early sharp money against the model)
"""

from __future__ import annotations

from dataclasses import dataclass, asdict, field
from typing import Dict, Optional

from .config import EdgeConfig, DEFAULT_CONFIG


def _sign(x: float) -> float:
    return (x > 0) - (x < 0)


def _clip(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


@dataclass
class EdgeMetrics:
    raw_edge: float
    calibrated_edge: float
    market_adjusted_edge: float
    drift_adjusted_edge: float
    sharp_adjusted_edge: float
    factors: Dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


class EdgeMetricEngine:
    def __init__(self, config: EdgeConfig = DEFAULT_CONFIG) -> None:
        self.cfg = config

    def compute(
        self,
        p_model: float,
        p_market: float,
        *,
        calibration_quality: float,
        mean_overround: float,
        prob_drift_total: Optional[float],
        prob_velocity: Optional[float],
        sharp_proxy: Optional[float],
    ) -> EdgeMetrics:
        if p_market <= 0:
            return EdgeMetrics(0.0, 0.0, 0.0, 0.0, 0.0, {})

        raw = p_model / p_market - 1.0

        f_cal = _clip(calibration_quality, 0.0, 1.0)
        calibrated = raw * f_cal

        f_mkt = _clip(1.0 / mean_overround if mean_overround else 1.0, 0.0, 1.0)
        market_adj = calibrated * f_mkt

        f_drift = self._drift_factor(raw, prob_drift_total, prob_velocity)
        drift_adj = market_adj * f_drift

        f_sharp = self._sharp_factor(raw, sharp_proxy)
        sharp_adj = drift_adj * f_sharp

        return EdgeMetrics(
            raw_edge=raw,
            calibrated_edge=calibrated,
            market_adjusted_edge=market_adj,
            drift_adjusted_edge=drift_adj,
            sharp_adjusted_edge=sharp_adj,
            factors={
                "f_cal": f_cal,
                "f_mkt": f_mkt,
                "f_drift": f_drift,
                "f_sharp": f_sharp,
            },
        )

    def _drift_factor(self, raw, prob_drift_total, prob_velocity) -> float:
        if prob_drift_total is None:
            contradiction = 0.0
        else:
            align = _sign(raw) * prob_drift_total
            contradiction = _clip(max(0.0, -align) / self.cfg.drift_contradiction_ref, 0.0, 1.0)
        if prob_velocity is None:
            stability = 1.0
        else:
            stability = _clip(
                1.0 - abs(prob_velocity) / self.cfg.drift_velocity_ref,
                self.cfg.drift_stability_floor, 1.0,
            )
        return _clip((1.0 - contradiction) * stability, 0.0, 1.0)

    def _sharp_factor(self, raw, sharp_proxy) -> float:
        if sharp_proxy is None:
            return 1.0
        align = _sign(raw) * sharp_proxy
        penalty = _clip(max(0.0, -align) / self.cfg.sharp_contradiction_ref, 0.0, 1.0)
        return 1.0 - penalty
