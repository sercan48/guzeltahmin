"""Truth layer (Phase-16 buildout).

Module M1 — canonicalization: the single, shared odds-format + de-vig primitive
that the Truth Store and all downstream consumers use. Pure / network-free.
"""

from .canonicalization import (
    OddsFormat,
    DevigMethod,
    DevigResult,
    to_decimal,
    devig,
    devig_multiplicative,
    devig_power,
    devig_shin,
    devig_ensemble,
)
from .store import (
    TruthStore,
    RawSnapshot,
    TruthRecord,
    ProviderClass,
    Provenance,
    classify_provider,
)
from .adapter import (
    TruthAdapter,
    MeasurementMode,
    TruthMeta,
    HybridValidation,
)
from .edge_wiring import (
    TruthEdgeAdjuster,
    TruthEdgeConfig,
    TruthAdjustedEdge,
    DEFAULT_TRUTH_EDGE_CONFIG,
)

__all__ = [
    "OddsFormat",
    "DevigMethod",
    "DevigResult",
    "to_decimal",
    "devig",
    "devig_multiplicative",
    "devig_power",
    "devig_shin",
    "devig_ensemble",
    "TruthStore",
    "RawSnapshot",
    "TruthRecord",
    "ProviderClass",
    "Provenance",
    "classify_provider",
    "TruthAdapter",
    "MeasurementMode",
    "TruthMeta",
    "HybridValidation",
    "TruthEdgeAdjuster",
    "TruthEdgeConfig",
    "TruthAdjustedEdge",
    "DEFAULT_TRUTH_EDGE_CONFIG",
]
