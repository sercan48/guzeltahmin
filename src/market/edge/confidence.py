"""Task 3 — Edge Confidence Engine.

How much to *trust* a measured edge, on [0, 1]. Confidence is independent of
edge magnitude (Task 2): a large edge with a contradicting market and a
poorly-calibrated model should be low-confidence.

    edge_confidence_score = sum_i w_i * c_i        (sum w_i = 1, each c_i in [0,1])

Components
----------
  c_cal   = calibration_quality                         (injected, per segment)
  c_eff   = market_consensus_score                       (reliability of the reference)
  c_disag = exp(-0.5 * ((|z| - z0)/sigma)^2)             (bump: meaningful but not absurd gap)
  c_drift = f_drift                                       (drift agreement + stability, Task 2)
  c_clv   = historical_clv_alignment                     (injected, per segment; 0.5 = neutral)
"""

from __future__ import annotations

import math
from dataclasses import dataclass, asdict, field
from typing import Dict, Optional

from .config import EdgeConfig, DEFAULT_CONFIG


@dataclass
class EdgeConfidence:
    edge_confidence_score: float
    components: Dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


class EdgeConfidenceEngine:
    def __init__(self, config: EdgeConfig = DEFAULT_CONFIG) -> None:
        self.cfg = config

    def compute(
        self,
        *,
        calibration_quality: float,
        market_consensus_score: Optional[float],
        gap_zscore: Optional[float],
        f_drift: float,
        clv_alignment: float,
    ) -> EdgeConfidence:
        c_cal = self._clip01(calibration_quality)
        c_eff = self._clip01(market_consensus_score if market_consensus_score is not None else 0.5)
        c_disag = self._disagreement_conf(gap_zscore)
        c_drift = self._clip01(f_drift)
        c_clv = self._clip01(clv_alignment)

        w = self.cfg.conf_weights
        score = (
            w["calibration"] * c_cal
            + w["efficiency"] * c_eff
            + w["disagreement"] * c_disag
            + w["drift_stability"] * c_drift
            + w["clv_alignment"] * c_clv
        )
        return EdgeConfidence(
            edge_confidence_score=self._clip01(score),
            components={
                "c_calibration": c_cal,
                "c_efficiency": c_eff,
                "c_disagreement": c_disag,
                "c_drift_stability": c_drift,
                "c_clv_alignment": c_clv,
            },
        )

    def _disagreement_conf(self, z: Optional[float]) -> float:
        if z is None:
            return 0.0
        z0 = self.cfg.disagreement_center_z
        sigma = self.cfg.disagreement_sigma_z
        return math.exp(-0.5 * ((abs(z) - z0) / sigma) ** 2)

    @staticmethod
    def _clip01(x: float) -> float:
        return max(0.0, min(1.0, x))
