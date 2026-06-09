"""M3 — Truth Rule Enforcement adapter.

The single sanctioned bridge between the Truth Store and the R1.2 measurement
layer. It converts canonical truth (p_truth / o_truth / confidence / provenance)
into the ``OddsRecord`` stream R1.2 already consumes — **without modifying R1.2,
R1.3, or the OddsRecord schema.**

Architecture rule enforced:
    Providers → Canonicalization (M1) → Truth Store (M2) → Truth Adapter (M3)
              → R1.2 Measurement → R1.3 Edge → future layers
Forbidden:  Provider → R1.2 / R1.3 / CLV / Portfolio (raw odds downstream).

Modes:
    TRUTH_ONLY : downstream sees only truth-sourced records (production target)
    HYBRID     : truth is canonical; raw consensus computed only to *validate*
                 (divergence reported, never fed downstream)
    LEGACY     : temporary passthrough of raw provider records (pre-migration)

Propagation: each emitted OddsRecord carries the truth confidence in
``confidence_score`` and the provenance in ``source_id`` (``truth:OBSERVED``).
A parallel ``truth_meta`` map exposes confidence/provenance/as_of per
(match, market, selection) for layers that want it explicitly (e.g. R1.3
``calibration_quality``) — again without touching OddsRecord.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Dict, List, Optional, Sequence, Tuple

from ..schema import OddsRecord, MatchContext
from ..measurement_pipeline import MeasurementPipeline, MeasurementResult
from ..schema import Horizon
from .store import TruthStore, TruthRecord, ProviderClass, classify_provider

# disagreement (sigma) at which cross-book agreement is considered fully eroded
_AGREEMENT_SIGMA_SCALE = 0.05

Key = Tuple[str, str, str]   # (match_id, market, selection)

# R1.2 consensus horizon -> Truth Store snapshot_type label (for like-for-like
# HYBRID validation; aligned with the schema SnapshotType values).
_HORIZON_TO_SNAPSHOT = {
    Horizon.OPENING: "OPEN",
    Horizon.H24: "T-24h",
    Horizon.H12: "T-12h",
    Horizon.H6: "T-6h",
    Horizon.H1: "T-1h",
    Horizon.CLOSING: "CLOSE",
}


class MeasurementMode(str, Enum):
    TRUTH_ONLY = "truth_only"
    HYBRID = "hybrid"
    LEGACY = "legacy"


@dataclass
class TruthMeta:
    confidence: float
    provenance: str
    as_of: str
    # derived truth-quality signals consumed by the M3.2 truth->edge layer
    truth_quality: float = 0.0              # == confidence (alias for clarity)
    truth_efficiency: float = 0.0           # cross-book agreement (1 - sigma/scale)
    sharp_consensus_strength: float = 0.0   # sharp trust-share x agreement, in [0,1]


@dataclass
class HybridValidation:
    """Divergence of raw-consensus vs truth (HYBRID mode; monitoring only)."""
    per_selection_gap: Dict[Key, float]
    max_abs_gap: float
    mean_abs_gap: float


class TruthAdapter:
    def __init__(self, store: TruthStore,
                 mode: MeasurementMode = MeasurementMode.TRUTH_ONLY) -> None:
        self.store = store
        self.mode = mode
        self.pipeline = MeasurementPipeline()

    # -- input construction -------------------------------------------------
    def build_inputs(
        self,
        contexts: Dict[str, MatchContext],
        market: Optional[str] = None,
        as_of: Optional[datetime] = None,
    ) -> Tuple[List[OddsRecord], Dict[Key, TruthMeta]]:
        """Truth-sourced OddsRecord stream + per-selection truth metadata.

        Only matches present in ``contexts`` (which carry kickoff) are emitted.
        ``as_of`` enforces point-in-time reads.
        """
        records: List[OddsRecord] = []
        meta: Dict[Key, TruthMeta] = {}
        for match_id in contexts:
            for tr in self.store.iter_truth(match_id=match_id, market=market, as_of=as_of):
                records.append(self._to_odds_record(tr))
                key = (tr.match_id, tr.market, tr.selection)
                # keep the latest (highest as_of) meta per selection
                if key not in meta or tr.as_of >= meta[key].as_of:
                    meta[key] = self._meta_from_record(tr)
        return records, meta

    def _meta_from_record(self, tr: TruthRecord) -> TruthMeta:
        agreement = max(0.0, min(1.0, 1.0 - tr.sigma_truth / _AGREEMENT_SIGMA_SCALE))
        sharp_share = sum(
            w for p, w in tr.contributing_providers.items()
            if classify_provider(p, self.store.class_overrides) == ProviderClass.SHARP.value
        )
        return TruthMeta(
            confidence=tr.confidence,
            provenance=tr.provenance,
            as_of=tr.as_of,
            truth_quality=tr.confidence,
            truth_efficiency=agreement,
            sharp_consensus_strength=max(0.0, min(1.0, sharp_share * agreement)),
        )

    @staticmethod
    def _to_odds_record(tr: TruthRecord) -> OddsRecord:
        return OddsRecord(
            match_id=tr.match_id,
            bookmaker="truth",
            market=tr.market,
            selection=tr.selection,
            odds=tr.o_truth,
            timestamp=datetime.fromisoformat(tr.as_of),
            snapshot_type=tr.snapshot_type,
            source_id=f"truth:{tr.provenance}",
            confidence_score=tr.confidence,
        )

    # -- measurement entry point -------------------------------------------
    def run_measurement(
        self,
        contexts: Dict[str, MatchContext],
        market: Optional[str] = None,
        as_of: Optional[datetime] = None,
        consensus_horizon: Horizon = Horizon.H1,
        raw_records: Optional[Sequence[OddsRecord]] = None,
    ) -> Tuple[MeasurementResult, Dict[Key, TruthMeta], Optional[HybridValidation]]:
        """Run R1.2 measurement over the truth stream (the enforced path).

        LEGACY mode (temporary) passes ``raw_records`` straight through.
        HYBRID mode runs on truth and additionally diffs raw consensus vs truth
        for monitoring (never feeding raw downstream).
        """
        if self.mode == MeasurementMode.LEGACY:
            if raw_records is None:
                raise ValueError("LEGACY mode requires raw_records")
            result = self.pipeline.run(list(raw_records), contexts, consensus_horizon)
            return result, {}, None

        records, meta = self.build_inputs(contexts, market, as_of)
        result = self.pipeline.run(records, contexts, consensus_horizon)

        validation = None
        if self.mode == MeasurementMode.HYBRID and raw_records:
            validation = self._validate_against_raw(raw_records, contexts, consensus_horizon)
        return result, meta, validation

    # -- hybrid validation (monitoring only) --------------------------------
    def _validate_against_raw(self, raw_records, contexts,
                              consensus_horizon) -> HybridValidation:
        raw_result = self.pipeline.run(list(raw_records), contexts, consensus_horizon)
        snap = _HORIZON_TO_SNAPSHOT.get(consensus_horizon)
        gaps: Dict[Key, float] = {}
        for em_key, eff in raw_result.efficiency.items():
            match_id, market = em_key.split("|", 1)
            for sel, p_raw in eff.consensus_prob.items():
                key = (match_id, market, sel)
                # compare like-for-like: raw consensus at the horizon vs truth
                # at the matching snapshot_type
                tr = self.store.get_truth(match_id, market, sel, snapshot_type=snap)
                if tr is not None:
                    gaps[key] = p_raw - tr.p_truth
        if not gaps:
            return HybridValidation({}, 0.0, 0.0)
        absvals = [abs(v) for v in gaps.values()]
        return HybridValidation(gaps, max(absvals), sum(absvals) / len(absvals))
