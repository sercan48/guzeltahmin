"""Edge Detection Kernel orchestrator (R1.3).

Consumes a R1.2 ``MeasurementResult`` + *injected* model probabilities (+ optional
per-segment metadata) and emits one ``EdgeResult`` per (match, market, selection):
comparator -> edge metrics -> confidence -> agreement -> EQS -> tier.

The existing prediction model is never called or modified here — model
probabilities are supplied by the caller.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Optional, Tuple

from .config import EdgeConfig, DEFAULT_CONFIG
from .comparator import ModelMarketComparator, ComparatorResult
from .metrics import EdgeMetricEngine, EdgeMetrics
from .confidence import EdgeConfidenceEngine, EdgeConfidence
from .agreement import MarketAgreementEngine, AgreementResult
from .eqs import EdgeQualityScorer, EQSResult
from .classification import SignalClassifier, ClassificationResult

Key = Tuple[str, str, str]   # (match_id, market, selection)


@dataclass
class SegmentMeta:
    """Injected, NOT learned here: properties of a market/segment.

    calibration_quality : how well the model is calibrated for this segment [0,1]
    clv_alignment       : historical fraction of this segment's edges that beat
                          the close [0,1]; 0.5 = neutral / unknown
    """
    calibration_quality: float
    clv_alignment: float


@dataclass
class EdgeResult:
    match_id: str
    market: str
    selection: str
    comparator: ComparatorResult
    metrics: EdgeMetrics
    confidence: EdgeConfidence
    agreement: AgreementResult
    eqs: EQSResult
    classification: ClassificationResult

    def to_dict(self) -> dict:
        return {
            "match_id": self.match_id,
            "market": self.market,
            "selection": self.selection,
            "comparator": self.comparator.to_dict(),
            "metrics": self.metrics.to_dict(),
            "confidence": self.confidence.to_dict(),
            "agreement": self.agreement.to_dict(),
            "eqs": self.eqs.to_dict(),
            "classification": self.classification.to_dict(),
        }


class EdgeDetectionKernel:
    def __init__(self, config: EdgeConfig = DEFAULT_CONFIG) -> None:
        self.cfg = config
        self.comparator = ModelMarketComparator()
        self.metrics = EdgeMetricEngine(config)
        self.confidence = EdgeConfidenceEngine(config)
        self.agreement = MarketAgreementEngine(config)
        self.eqs = EdgeQualityScorer(config)
        self.classifier = SignalClassifier(config)

    def run(
        self,
        measurement_result,
        model_probs: Dict[Key, float],
        segment_meta: Optional[Dict[str, SegmentMeta]] = None,
        hist_gap_mean: Optional[float] = None,
        hist_gap_std: Optional[float] = None,
    ) -> Dict[Key, EdgeResult]:
        segment_meta = segment_meta or {}

        # market_probability = de-vigged consensus from R1.2 efficiency
        market_probs: Dict[Key, float] = {}
        for em_key, eff in measurement_result.efficiency.items():
            match_id, market = em_key.split("|", 1)
            for sel, p in eff.consensus_prob.items():
                market_probs[(match_id, market, sel)] = p

        comp = self.comparator.compare_batch(
            model_probs, market_probs, hist_gap_mean, hist_gap_std
        )

        out: Dict[Key, EdgeResult] = {}
        for key, c in comp.items():
            match_id, market, sel = key
            eff = measurement_result.efficiency.get(f"{match_id}|{market}")
            drift = measurement_result.drift.get(f"{match_id}|{market}|{sel}@consensus")
            meta = segment_meta.get(match_id) or segment_meta.get(f"{match_id}|{market}") or \
                SegmentMeta(self.cfg.default_calibration_quality, self.cfg.default_clv_alignment)

            mean_overround = eff.mean_overround if eff and eff.mean_overround else 1.0
            consensus_score = eff.market_consensus_score if eff else None
            sharp_proxy = eff.sharp_proxy.get(sel) if eff else None
            prob_drift_total = drift.prob_drift_total if drift else None
            prob_velocity = drift.prob_velocity if drift else None

            m = self.metrics.compute(
                c.model_probability, c.market_probability,
                calibration_quality=meta.calibration_quality,
                mean_overround=mean_overround,
                prob_drift_total=prob_drift_total,
                prob_velocity=prob_velocity,
                sharp_proxy=sharp_proxy,
            )
            conf = self.confidence.compute(
                calibration_quality=meta.calibration_quality,
                market_consensus_score=consensus_score,
                gap_zscore=c.probability_gap_zscore,
                f_drift=m.factors["f_drift"],
                clv_alignment=meta.clv_alignment,
            )
            agr = self.agreement.classify(
                c.probability_gap, c.probability_gap_zscore, prob_drift_total
            )
            eqs = self.eqs.compute(
                sharp_adjusted_edge=m.sharp_adjusted_edge,
                edge_confidence_score=conf.edge_confidence_score,
                clv_alignment=meta.clv_alignment,
            )
            cls = self.classifier.classify(
                eqs=eqs.eqs,
                edge_confidence_score=conf.edge_confidence_score,
                agreement_class=agr.agreement_class,
                sharp_adjusted_edge=m.sharp_adjusted_edge,
                f_drift=m.factors["f_drift"],
                f_sharp=m.factors["f_sharp"],
            )
            out[key] = EdgeResult(match_id, market, sel, c, m, conf, agr, eqs, cls)
        return out
