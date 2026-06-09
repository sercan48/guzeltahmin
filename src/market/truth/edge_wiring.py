"""M3.2 — Truth -> Edge wiring (discount-only).

Completes the Truth chain: it takes an R1.3 ``EdgeResult`` and the truth
metadata for that selection, and applies a **discount-only** adjustment to the
headline edge, then re-scores EQS/tier by *reusing* the existing edge-kernel
components (EdgeQualityScorer, SignalClassifier) unchanged.

HARD GUARANTEE (discount-only):
    edge_after_truth = edge_before_truth * truth_discount,   truth_discount in (0, 1]
    => for a positive edge, |edge_after| <= |edge_before|.  Edge is NEVER increased.

NO redesign of the edge kernel: this is a new, additive post-processing layer.
R1.2, R1.3 internals, OddsRecord, CLV, Portfolio, and thresholds are untouched.

Discount factors (each in (0, 1], product <= 1):
  confidence_discount = c_floor + (1 - c_floor) * clip(confidence, 0, 1)
  provenance_discount = { OBSERVED: 1.0, PARTIAL: 0.80, RECONSTRUCTED: 0.50, unknown: 0.50 }
  sharp_discount      = s_floor + (1 - s_floor) * clip(sharp_consensus_strength, 0, 1)
  truth_discount      = confidence_discount * provenance_discount * sharp_discount
"""

from __future__ import annotations

from dataclasses import dataclass, asdict, field
from typing import Dict, Optional, Tuple

from ..edge.eqs import EdgeQualityScorer
from ..edge.classification import SignalClassifier
from ..edge.pipeline import EdgeResult
from .adapter import TruthMeta
from .store import Provenance

Key = Tuple[str, str, str]


@dataclass(frozen=True)
class TruthEdgeConfig:
    confidence_floor: float = 0.30          # discount at zero confidence
    sharp_floor: float = 0.40               # discount at zero sharp consensus
    provenance_factor: Dict[str, float] = field(default_factory=lambda: {
        Provenance.OBSERVED.value: 1.00,
        "PARTIAL": 0.80,
        Provenance.RECONSTRUCTED.value: 0.50,
    })
    provenance_unknown: float = 0.50        # conservative default


DEFAULT_TRUTH_EDGE_CONFIG = TruthEdgeConfig()


@dataclass
class TruthAdjustedEdge:
    match_id: str
    market: str
    selection: str
    # monitoring outputs (required)
    edge_before_truth: float
    edge_after_truth: float
    truth_discount: float
    confidence_discount: float
    provenance_discount: float
    sharp_consensus_discount: float
    # re-scored with the unchanged edge-kernel components
    eqs_before: float
    eqs_after: float
    tier_before: str
    tier_after: str
    provenance: str
    truth_confidence: float

    def to_dict(self) -> dict:
        return asdict(self)


def _clip01(x: float) -> float:
    return max(0.0, min(1.0, x))


class TruthEdgeAdjuster:
    """Applies the truth discount to edge results. Discount-only, additive."""

    def __init__(self, config: TruthEdgeConfig = DEFAULT_TRUTH_EDGE_CONFIG) -> None:
        self.cfg = config
        self.scorer = EdgeQualityScorer()        # reused, unchanged
        self.classifier = SignalClassifier()      # reused, unchanged

    # -- discount factors ---------------------------------------------------
    def confidence_discount(self, confidence: float) -> float:
        c = _clip01(confidence)
        return self.cfg.confidence_floor + (1.0 - self.cfg.confidence_floor) * c

    def provenance_discount(self, provenance: str) -> float:
        return self.cfg.provenance_factor.get(provenance, self.cfg.provenance_unknown)

    def sharp_consensus_discount(self, strength: float) -> float:
        s = _clip01(strength)
        return self.cfg.sharp_floor + (1.0 - self.cfg.sharp_floor) * s

    # -- adjust one --------------------------------------------------------
    def adjust(self, edge: EdgeResult, meta: Optional[TruthMeta]) -> TruthAdjustedEdge:
        edge_before = edge.metrics.sharp_adjusted_edge
        ecs = edge.confidence.edge_confidence_score
        clv = edge.eqs.components.get("clv_alignment", 0.5)
        f_drift = edge.metrics.factors.get("f_drift", 1.0)
        f_sharp = edge.metrics.factors.get("f_sharp", 1.0)
        agreement_class = edge.agreement.agreement_class

        if meta is None:
            # no truth metadata -> conservative: treat as low trust (strong discount)
            c_disc = self.confidence_discount(0.0)
            p_disc = self.cfg.provenance_unknown
            s_disc = self.sharp_consensus_discount(0.0)
            provenance, confidence = "NONE", 0.0
        else:
            c_disc = self.confidence_discount(meta.confidence)
            p_disc = self.provenance_discount(meta.provenance)
            s_disc = self.sharp_consensus_discount(meta.sharp_consensus_strength)
            provenance, confidence = meta.provenance, meta.confidence

        truth_discount = _clip01(c_disc * p_disc * s_disc)   # product of <=1 factors
        edge_after = edge_before * truth_discount

        # re-score with the UNCHANGED kernel components
        eqs_after = self.scorer.compute(
            sharp_adjusted_edge=edge_after,
            edge_confidence_score=ecs,
            clv_alignment=clv,
        )
        cls_after = self.classifier.classify(
            eqs=eqs_after.eqs,
            edge_confidence_score=ecs,
            agreement_class=agreement_class,
            sharp_adjusted_edge=edge_after,
            f_drift=f_drift,
            f_sharp=f_sharp,
        )

        return TruthAdjustedEdge(
            match_id=edge.match_id, market=edge.market, selection=edge.selection,
            edge_before_truth=edge_before,
            edge_after_truth=edge_after,
            truth_discount=truth_discount,
            confidence_discount=c_disc,
            provenance_discount=p_disc,
            sharp_consensus_discount=s_disc,
            eqs_before=edge.eqs.eqs,
            eqs_after=eqs_after.eqs,
            tier_before=edge.classification.tier,
            tier_after=cls_after.tier,
            provenance=provenance,
            truth_confidence=confidence,
        )

    # -- adjust a batch ----------------------------------------------------
    def adjust_batch(
        self,
        edges: Dict[Key, EdgeResult],
        metas: Dict[Key, TruthMeta],
    ) -> Dict[Key, TruthAdjustedEdge]:
        return {k: self.adjust(e, metas.get(k)) for k, e in edges.items()}
