"""Task 4 — Market Agreement Framework.

Classifies every prediction into exactly one of four classes from the signed
probability gap g = p_model - p_market, its z-score, and the market's own
movement (prob_drift_total).

Exact decision rules (evaluated top-down, first match wins)
-----------------------------------------------------------
  align = sign(g) * prob_drift_total                       (market moving with/against model)

  D  CONFLICT_ZONE       if |z| >= conflict_z
                         OR (|g| > agree_gap_tol AND align < conflict_drift_align)
  A  MODEL_MARKET_AGREE  elif |g| <= agree_gap_tol OR |z| < agree_z_lo
  B  MODEL_STRONGER      elif g > 0           (model assigns higher prob than market)
  C  MARKET_STRONGER     else                 (g < 0; market assigns higher prob)

Defaults: agree_gap_tol=0.02, agree_z_lo=1.0, conflict_z=2.5,
conflict_drift_align=-0.02.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict, field
from enum import Enum
from typing import List, Optional

from .config import EdgeConfig, DEFAULT_CONFIG


class AgreementClass(str, Enum):
    AGREE = "A_MODEL_MARKET_AGREE"
    MODEL_STRONGER = "B_MODEL_STRONGER"
    MARKET_STRONGER = "C_MARKET_STRONGER"
    CONFLICT = "D_CONFLICT_ZONE"


def _sign(x: float) -> float:
    return (x > 0) - (x < 0)


@dataclass
class AgreementResult:
    agreement_class: str
    gap: float
    zscore: Optional[float]
    drift_align: Optional[float]
    reasons: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


class MarketAgreementEngine:
    def __init__(self, config: EdgeConfig = DEFAULT_CONFIG) -> None:
        self.cfg = config

    def classify(
        self,
        gap: float,
        zscore: Optional[float],
        prob_drift_total: Optional[float],
    ) -> AgreementResult:
        cfg = self.cfg
        align = None if prob_drift_total is None else _sign(gap) * prob_drift_total
        reasons: List[str] = []

        # D — Conflict
        if zscore is not None and abs(zscore) >= cfg.conflict_z:
            reasons.append(f"|z|={abs(zscore):.2f} >= conflict_z={cfg.conflict_z} (implausible gap)")
            return AgreementResult(AgreementClass.CONFLICT.value, gap, zscore, align, reasons)
        if abs(gap) > cfg.agree_gap_tol and align is not None and align < cfg.conflict_drift_align:
            reasons.append(
                f"market moving against model: align={align:.3f} < {cfg.conflict_drift_align}"
            )
            return AgreementResult(AgreementClass.CONFLICT.value, gap, zscore, align, reasons)

        # A — Agree
        if abs(gap) <= cfg.agree_gap_tol or (zscore is not None and abs(zscore) < cfg.agree_z_lo):
            reasons.append(
                f"|gap|={abs(gap):.3f} <= {cfg.agree_gap_tol} or |z| < {cfg.agree_z_lo}"
            )
            return AgreementResult(AgreementClass.AGREE.value, gap, zscore, align, reasons)

        # B / C
        if gap > 0:
            reasons.append("model assigns higher probability than market")
            return AgreementResult(AgreementClass.MODEL_STRONGER.value, gap, zscore, align, reasons)
        reasons.append("market assigns higher probability than model")
        return AgreementResult(AgreementClass.MARKET_STRONGER.value, gap, zscore, align, reasons)
