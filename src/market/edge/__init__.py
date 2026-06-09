"""MIW R1.3 — Edge Detection Kernel.

The first true edge-detection layer. Measures disagreement between (1) model
probability, (2) market implied probability, and (3) market movement behaviour,
then scores and classifies the opportunity — WITHOUT touching the prediction
models, optimizing thresholds, sizing stakes (no Kelly), or learning.

Built on the R1.2 measurement layer (drift / CLV / efficiency / integrity) and
the R1.1 PAL contract. Model probabilities are *injected*; nothing here trains.

Public surface
--------------
    EdgeDetectionKernel, EdgeResult, SegmentMeta      (pipeline)
    ModelMarketComparator                              (Task 1)
    EdgeMetricEngine                                   (Task 2)
    EdgeConfidenceEngine                               (Task 3)
    MarketAgreementEngine, AgreementClass              (Task 4)
    HistoricalValidator, SettledRecord                 (Task 5)
    EdgeQualityScorer                                  (Task 6)
    SignalClassifier, SignalTier                       (Task 7)
    EdgeConfig, DEFAULT_CONFIG                          (all thresholds)
"""

from .config import EdgeConfig, DEFAULT_CONFIG
from .comparator import ModelMarketComparator, ComparatorResult
from .metrics import EdgeMetricEngine, EdgeMetrics
from .confidence import EdgeConfidenceEngine, EdgeConfidence
from .agreement import MarketAgreementEngine, AgreementResult, AgreementClass
from .validation import HistoricalValidator, SettledRecord, BucketMetrics
from .eqs import EdgeQualityScorer, EQSResult
from .classification import SignalClassifier, ClassificationResult, SignalTier
from .pipeline import EdgeDetectionKernel, EdgeResult, SegmentMeta

__all__ = [
    "EdgeConfig", "DEFAULT_CONFIG",
    "ModelMarketComparator", "ComparatorResult",
    "EdgeMetricEngine", "EdgeMetrics",
    "EdgeConfidenceEngine", "EdgeConfidence",
    "MarketAgreementEngine", "AgreementResult", "AgreementClass",
    "HistoricalValidator", "SettledRecord", "BucketMetrics",
    "EdgeQualityScorer", "EQSResult",
    "SignalClassifier", "ClassificationResult", "SignalTier",
    "EdgeDetectionKernel", "EdgeResult", "SegmentMeta",
]
