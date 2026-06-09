"""Task 6 — Edge Quality Score (EQS), range 0-100.

A single, interpretable headline number that blends realizable edge magnitude,
trust in that edge, and historical CLV alignment.

    edge_n = clip(sharp_adjusted_edge / eqs_edge_ref, 0, 1)     (only positive edges score)
    EQS = 100 * ( w_edge * edge_n
                + w_conf * edge_confidence_score
                + w_clv  * clv_alignment )

Gate: a non-positive headline edge (sharp_adjusted_edge <= 0) yields EQS = 0 —
there is no backable edge to grade, regardless of confidence.

Interpretation
--------------
  85-100  exceptional: large, well-calibrated, market/sharp-confirmed edge
  70-84   strong
  55-69   moderate
  40-54   marginal
   0-39   weak / no edge
Defaults: w_edge=0.45, w_conf=0.40, w_clv=0.15, eqs_edge_ref=0.10 (10% edge = full).
"""

from __future__ import annotations

from dataclasses import dataclass, asdict, field
from typing import Dict

from .config import EdgeConfig, DEFAULT_CONFIG


@dataclass
class EQSResult:
    eqs: float
    components: Dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


class EdgeQualityScorer:
    def __init__(self, config: EdgeConfig = DEFAULT_CONFIG) -> None:
        self.cfg = config

    def compute(
        self,
        *,
        sharp_adjusted_edge: float,
        edge_confidence_score: float,
        clv_alignment: float,
    ) -> EQSResult:
        if sharp_adjusted_edge <= 0:
            return EQSResult(0.0, {
                "edge_n": 0.0,
                "confidence": edge_confidence_score,
                "clv_alignment": clv_alignment,
                "gated": 1.0,
            })
        edge_n = max(0.0, min(1.0, sharp_adjusted_edge / self.cfg.eqs_edge_ref))
        w = self.cfg.eqs_weights
        score = 100.0 * (
            w["edge"] * edge_n
            + w["confidence"] * edge_confidence_score
            + w["clv_alignment"] * clv_alignment
        )
        return EQSResult(
            eqs=round(max(0.0, min(100.0, score)), 2),
            components={
                "edge_n": round(edge_n, 4),
                "confidence": round(edge_confidence_score, 4),
                "clv_alignment": round(clv_alignment, 4),
                "gated": 0.0,
            },
        )
