"""M5 — Pipeline Orchestrator & Paper Activation.

Connects M1-M4 into one routed, state-gated, idempotent flow:

    Trigger -> Lifecycle event(s) [M4] -> (if state allows) pipeline:
        Truth Store [M2] -> Truth Adapter [M3] -> Measurement [R1.2]
        -> Edge [R1.3] -> Truth Adjustment [M3.2] -> PaperSignal

The orchestrator contains **no prediction logic and no betting logic** — model
probabilities are *injected* via a provider callback; there is no bankroll, no
Kelly, no stake sizing. Paper mode only: it emits signal candidates and records
them.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from typing import Callable, Dict, List, Optional, Tuple

from ..schema import MatchContext, Horizon
from ..edge import EdgeDetectionKernel, SegmentMeta
from ..truth import (
    TruthStore, RawSnapshot, TruthAdapter, MeasurementMode, TruthEdgeAdjuster,
)
from .lifecycle import EventType, State, Event, Outcome
from .event_store import LifecycleService

Key = Tuple[str, str, str]
_TIER_RANK = {"REJECT": 0, "TIER_C": 1, "TIER_B": 2, "TIER_A": 3, "TIER_S": 4}


class TriggerType(str, Enum):
    MATCH_CREATED = "MATCH_CREATED"
    ODDS_UPDATED = "ODDS_UPDATED"
    SNAPSHOT_CAPTURED = "SNAPSHOT_CAPTURED"
    MATCH_STARTED = "MATCH_STARTED"
    MATCH_FINISHED = "MATCH_FINISHED"
    SETTLEMENT_COMPLETED = "SETTLEMENT_COMPLETED"


# Trigger -> ordered lifecycle events to emit. The state machine (M4) enforces
# legality/idempotency; the orchestrator only routes.
_TRIGGER_LIFECYCLE: Dict[TriggerType, List[EventType]] = {
    TriggerType.MATCH_CREATED: [EventType.MATCH_CREATED],
    TriggerType.ODDS_UPDATED: [EventType.ODDS_UPDATED],
    TriggerType.SNAPSHOT_CAPTURED: [EventType.ODDS_UPDATED],
    TriggerType.MATCH_STARTED: [EventType.MARKET_LOCKED, EventType.MATCH_STARTED],
    TriggerType.MATCH_FINISHED: [EventType.MATCH_FINISHED],
    TriggerType.SETTLEMENT_COMPLETED: [
        EventType.MATCH_FINISHED, EventType.RESULT_CONFIRMED, EventType.SETTLEMENT_COMPLETED,
    ],
}
# triggers that drive signal generation
_SIGNAL_TRIGGERS = {TriggerType.ODDS_UPDATED, TriggerType.SNAPSHOT_CAPTURED}


@dataclass
class Trigger:
    match_id: str
    type: TriggerType
    idempotency_key: str
    occurred_at: datetime
    payload: dict = field(default_factory=dict)

    def __post_init__(self):
        if isinstance(self.type, str):
            self.type = TriggerType(self.type)
        if self.occurred_at.tzinfo is None:
            self.occurred_at = self.occurred_at.replace(tzinfo=timezone.utc)


@dataclass
class PaperSignal:
    """Paper-mode signal candidate. NO bankroll / Kelly / stake / real betting."""
    match_id: str
    market: str
    selection: str
    edge_score: float          # truth-adjusted edge (M3.2)
    tier: str                  # truth-adjusted tier
    confidence: float          # edge confidence (R1.3)
    truth_confidence: float    # truth-store confidence (M2)
    timestamp: str             # ISO UTC

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class OrchestratorMetrics:
    signals_generated: int = 0
    signals_blocked: int = 0
    duplicate_signals: int = 0
    pipeline_failures: int = 0
    execution_latency_total: float = 0.0
    execution_latency_last: float = 0.0
    triggers_handled: int = 0

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class OrchestratorConfig:
    mode: str = "paper"
    active_min_tier: str = "TIER_B"     # ACTIVE state: "limited" -> only >= this tier
    max_retries: int = 1                # bounded retry for the pipeline action
    consensus_horizon: Horizon = Horizon.H1


@dataclass
class TriggerResult:
    trigger: TriggerType
    state: Optional[str]
    lifecycle_outcomes: List[str]
    signals: List[PaperSignal] = field(default_factory=list)
    blocked: bool = False
    failed: bool = False
    note: str = ""


ModelProbProvider = Callable[[str, str], Dict[str, float]]      # (match, market) -> {sel: p}
SegmentMetaProvider = Callable[[str], Optional[SegmentMeta]]    # match -> SegmentMeta|None


class PipelineOrchestrator:
    def __init__(
        self,
        store: TruthStore,
        lifecycle: LifecycleService,
        model_prob_provider: ModelProbProvider,
        segment_meta_provider: Optional[SegmentMetaProvider] = None,
        config: OrchestratorConfig = OrchestratorConfig(),
    ) -> None:
        self.store = store
        self.lifecycle = lifecycle
        self.model_prob_provider = model_prob_provider
        self.segment_meta_provider = segment_meta_provider
        self.cfg = config
        self.truth_adapter = TruthAdapter(store, MeasurementMode.TRUTH_ONLY)
        self.kernel = EdgeDetectionKernel()
        self.adjuster = TruthEdgeAdjuster()
        self.contexts: Dict[str, MatchContext] = {}
        self.paper_signals: List[PaperSignal] = []
        self.metrics = OrchestratorMetrics()

    # -- public entry point -------------------------------------------------
    def handle_trigger(self, trig: Trigger) -> TriggerResult:
        self.metrics.triggers_handled += 1

        # register context on creation (kickoff needed for measurement)
        if trig.type == TriggerType.MATCH_CREATED:
            self._register_context(trig)

        # route to lifecycle (M4 enforces legality/idempotency)
        outcomes = self._emit_lifecycle(trig)
        agg = self.lifecycle.get(trig.match_id)

        # ingest snapshots into the Truth Store on odds/snapshot triggers
        if trig.type in _SIGNAL_TRIGGERS:
            self._ingest_and_recompute(trig)

        result = TriggerResult(trig.type, agg.state.value if agg.state else None,
                               [o.value for o in outcomes])

        # signal generation is gated by lifecycle state
        if trig.type in _SIGNAL_TRIGGERS:
            if not agg.can_generate_signal():
                self.metrics.signals_blocked += 1
                result.blocked = True
                result.note = f"signals blocked in state {agg.state}"
                return result
            try:
                result.signals = self._run_with_retry(trig, agg.state)
            except Exception as exc:   # pragma: no cover - defensive
                self.metrics.pipeline_failures += 1
                result.failed = True
                result.note = f"pipeline failure: {exc}"
        return result

    # -- lifecycle routing --------------------------------------------------
    def _emit_lifecycle(self, trig: Trigger) -> List[Outcome]:
        outcomes = []
        for etype in _TRIGGER_LIFECYCLE[trig.type]:
            ev = Event(
                match_id=trig.match_id, type=etype,
                idempotency_key=f"{trig.idempotency_key}:{etype.value}",
                occurred_at=trig.occurred_at, payload={},
            )
            outcomes.append(self.lifecycle.handle(ev).outcome)
        return outcomes

    def _register_context(self, trig: Trigger) -> None:
        ko = trig.payload.get("kickoff")
        if isinstance(ko, str):
            ko = datetime.fromisoformat(ko)
        if ko is None:
            ko = trig.occurred_at
        self.contexts[trig.match_id] = MatchContext(
            trig.match_id, ko, label=trig.payload.get("label", ""))

    # -- truth ingest -------------------------------------------------------
    def _ingest_and_recompute(self, trig: Trigger) -> None:
        snaps = trig.payload.get("snapshots", [])
        touched: set = set()
        for s in snaps:
            self.store.ingest_snapshot(RawSnapshot(
                match_id=trig.match_id, provider=s["provider"], market=s["market"],
                selection=s["selection"], odds=float(s["odds"]),
                snapshot_type=s.get("snapshot_type", "OPEN"),
                collected_at=trig.occurred_at, provider_class=s.get("provider_class"),
            ))
            touched.add((s["market"], s.get("snapshot_type", "OPEN")))
        for market, stype in touched:
            self.store.recompute_truth(trig.match_id, market, stype)

    # -- pipeline (idempotent, retry-bounded) ------------------------------
    def _run_with_retry(self, trig: Trigger, state: State) -> List[PaperSignal]:
        last_exc = None
        for _ in range(self.cfg.max_retries + 1):
            try:
                return self._generate_signals(trig, state)
            except Exception as exc:   # transient -> retry (idempotent)
                last_exc = exc
        raise last_exc   # pragma: no cover

    def _generate_signals(self, trig: Trigger, state: State) -> List[PaperSignal]:
        ctx = self.contexts.get(trig.match_id)
        if ctx is None:
            return []   # no kickoff context -> cannot anchor measurement (graceful)

        t0 = time.perf_counter()
        contexts = {trig.match_id: ctx}
        result, truth_meta, _ = self.truth_adapter.run_measurement(
            contexts, consensus_horizon=self.cfg.consensus_horizon)

        # injected model probabilities (no prediction logic here)
        model_probs: Dict[Key, float] = {}
        for em_key in result.efficiency:
            match_id, market = em_key.split("|", 1)
            for sel, p in self.model_prob_provider(match_id, market).items():
                model_probs[(match_id, market, sel)] = p
        if not model_probs:
            self._record_latency(t0)
            return []

        seg = None
        if self.segment_meta_provider:
            seg = self.segment_meta_provider(trig.match_id)
        seg_map = {trig.match_id: seg} if seg else None

        edges = self.kernel.run(result, model_probs, seg_map)
        adjusted = self.adjuster.adjust_batch(edges, truth_meta)

        signals: List[PaperSignal] = []
        for key, adj in adjusted.items():
            if adj.tier_after == "REJECT":
                continue
            # ACTIVE state is "limited": only emit >= active_min_tier
            if state == State.ACTIVE and _TIER_RANK[adj.tier_after] < _TIER_RANK[self.cfg.active_min_tier]:
                self.metrics.signals_blocked += 1
                continue
            match_id, market, sel = key
            tc = truth_meta.get(key)
            sig = PaperSignal(
                match_id=match_id, market=market, selection=sel,
                edge_score=adj.edge_after_truth, tier=adj.tier_after,
                confidence=edges[key].confidence.edge_confidence_score,
                truth_confidence=tc.confidence if tc else 0.0,
                timestamp=trig.occurred_at.astimezone(timezone.utc).isoformat(),
            )
            if self._emit_signal_event(trig, key):
                self.paper_signals.append(sig)
                self.metrics.signals_generated += 1
                signals.append(sig)
            else:
                self.metrics.duplicate_signals += 1
        self._record_latency(t0)
        return signals

    def _emit_signal_event(self, trig: Trigger, key: Key) -> bool:
        """Emit SIGNAL_GENERATED; returns False if a duplicate (dedup)."""
        match_id, market, sel = key
        skey = f"signal:{match_id}:{market}:{sel}:{trig.idempotency_key}"
        ev = Event(match_id, EventType.SIGNAL_GENERATED, skey, trig.occurred_at, {})
        return self.lifecycle.handle(ev).outcome != Outcome.DUPLICATE

    def _record_latency(self, t0: float) -> None:
        dt = time.perf_counter() - t0
        self.metrics.execution_latency_last = dt
        self.metrics.execution_latency_total += dt
