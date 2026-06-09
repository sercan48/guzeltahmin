"""Task 7 — Signal Classification into Tier S / A / B / C / Reject.

Exact rules (top-down, first match wins). Inputs: EQS, edge_confidence_score
(ECS), agreement class, headline edge sign, and the confirming factors
f_drift / f_sharp from Task 2.

  REJECT   if sharp_adjusted_edge <= 0                      (no backable edge)
           OR agreement == D (CONFLICT)                     (model vs market clash)
           OR ECS < conf_reject_below (0.35)                (untrustworthy)
           OR EQS < eqs_reject_below (40)                   (low quality)

  TIER S   if EQS >= 85 AND ECS >= 0.70
           AND agreement == B (MODEL_STRONGER)
           AND f_drift >= 0.90 AND f_sharp >= 0.90          (market + sharp confirm)

  TIER A   elif EQS >= 70
  TIER B   elif EQS >= 55
  TIER C   elif EQS >= 40

Class C (MARKET_STRONGER) and class A (AGREE) generally fail the positive-edge
gate and reject; only class B can reach Tier S. This kernel measures/classifies
only — it never sizes a stake.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict, field
from enum import Enum
from typing import List

from .config import EdgeConfig, DEFAULT_CONFIG
from .agreement import AgreementClass


class SignalTier(str, Enum):
    S = "TIER_S"
    A = "TIER_A"
    B = "TIER_B"
    C = "TIER_C"
    REJECT = "REJECT"


@dataclass
class ClassificationResult:
    tier: str
    reasons: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


class SignalClassifier:
    def __init__(self, config: EdgeConfig = DEFAULT_CONFIG) -> None:
        self.cfg = config

    def classify(
        self,
        *,
        eqs: float,
        edge_confidence_score: float,
        agreement_class: str,
        sharp_adjusted_edge: float,
        f_drift: float,
        f_sharp: float,
    ) -> ClassificationResult:
        cfg = self.cfg
        reasons: List[str] = []

        # --- reject gates ---
        if sharp_adjusted_edge <= 0:
            reasons.append("no backable edge (sharp_adjusted_edge <= 0)")
            return ClassificationResult(SignalTier.REJECT.value, reasons)
        if agreement_class == AgreementClass.CONFLICT.value:
            reasons.append("conflict zone (model vs market movement clash)")
            return ClassificationResult(SignalTier.REJECT.value, reasons)
        if edge_confidence_score < cfg.conf_reject_below:
            reasons.append(f"confidence {edge_confidence_score:.2f} < {cfg.conf_reject_below}")
            return ClassificationResult(SignalTier.REJECT.value, reasons)
        if eqs < cfg.eqs_reject_below:
            reasons.append(f"EQS {eqs:.1f} < {cfg.eqs_reject_below}")
            return ClassificationResult(SignalTier.REJECT.value, reasons)

        # --- tier S (strict) ---
        if (
            eqs >= cfg.eqs_tier_s
            and edge_confidence_score >= cfg.tier_s_min_conf
            and agreement_class == AgreementClass.MODEL_STRONGER.value
            and f_drift >= cfg.tier_s_min_confirm
            and f_sharp >= cfg.tier_s_min_confirm
        ):
            reasons.append("EQS>=85, ECS>=0.70, class B, drift+sharp confirm")
            return ClassificationResult(SignalTier.S.value, reasons)

        # --- graded tiers ---
        if eqs >= cfg.eqs_tier_a:
            reasons.append(f"EQS {eqs:.1f} in [70,85)")
            return ClassificationResult(SignalTier.A.value, reasons)
        if eqs >= cfg.eqs_tier_b:
            reasons.append(f"EQS {eqs:.1f} in [55,70)")
            return ClassificationResult(SignalTier.B.value, reasons)
        reasons.append(f"EQS {eqs:.1f} in [40,55)")
        return ClassificationResult(SignalTier.C.value, reasons)
