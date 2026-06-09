"""M4 — Market lifecycle state machine (deterministic, event-sourced).

Orchestration only. NO prediction logic. This is the backbone that walks each
match through its lifecycle so the rest of the pipeline (truth -> measurement ->
edge) runs at the right time, idempotently, and recoverably.

States:   PREMATCH -> ACTIVE -> LOCKED -> CLOSED -> SETTLED  (+ VOID, CANCELLED)
Events:   MATCH_CREATED, ODDS_UPDATED, SIGNAL_GENERATED, MATCH_STARTED,
          MARKET_LOCKED, MATCH_FINISHED, RESULT_CONFIRMED, SETTLEMENT_COMPLETED
          (+ MATCH_VOIDED, MATCH_CANCELLED)

Determinism: folding the same ordered event sequence always yields the same
final state and the same observability counters (replay == reconstruction).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Callable, Dict, List, Optional, Set, Tuple


class State(str, Enum):
    PREMATCH = "PREMATCH"
    ACTIVE = "ACTIVE"
    LOCKED = "LOCKED"
    CLOSED = "CLOSED"
    SETTLED = "SETTLED"
    VOID = "VOID"
    CANCELLED = "CANCELLED"


TERMINAL_STATES = {State.SETTLED, State.VOID, State.CANCELLED}


class EventType(str, Enum):
    MATCH_CREATED = "MATCH_CREATED"
    ODDS_UPDATED = "ODDS_UPDATED"
    SIGNAL_GENERATED = "SIGNAL_GENERATED"
    MATCH_STARTED = "MATCH_STARTED"
    MARKET_LOCKED = "MARKET_LOCKED"
    MATCH_FINISHED = "MATCH_FINISHED"
    RESULT_CONFIRMED = "RESULT_CONFIRMED"
    SETTLEMENT_COMPLETED = "SETTLEMENT_COMPLETED"
    MATCH_VOIDED = "MATCH_VOIDED"
    MATCH_CANCELLED = "MATCH_CANCELLED"


class Outcome(str, Enum):
    APPLIED = "APPLIED"          # state-changing transition applied
    IDEMPOTENT = "IDEMPOTENT"    # event valid but already in target state / data no-op
    DUPLICATE = "DUPLICATE"      # idempotency key already seen
    INVALID = "INVALID"          # illegal transition for current state (rejected)


# --- canonical state-changing transitions: (from_state | None, event) -> to ---
_BASE_TRANSITIONS: Dict[Tuple[Optional[State], EventType], State] = {
    (None, EventType.MATCH_CREATED): State.PREMATCH,
    (State.PREMATCH, EventType.ODDS_UPDATED): State.ACTIVE,     # first odds activates
    (State.PREMATCH, EventType.MARKET_LOCKED): State.LOCKED,     # locked without trading
    (State.ACTIVE, EventType.MARKET_LOCKED): State.LOCKED,
    (State.LOCKED, EventType.MATCH_STARTED): State.CLOSED,       # kickoff -> in-play/closed
    (State.CLOSED, EventType.SETTLEMENT_COMPLETED): State.SETTLED,
}

# void / cancel edges (from many states)
for _s in (State.PREMATCH, State.ACTIVE, State.LOCKED, State.CLOSED):
    _BASE_TRANSITIONS[(_s, EventType.MATCH_VOIDED)] = State.VOID
for _s in (State.PREMATCH, State.ACTIVE, State.LOCKED):
    _BASE_TRANSITIONS[(_s, EventType.MATCH_CANCELLED)] = State.CANCELLED

# --- data events allowed in a state (recorded, no state change) ---------------
_DATA_EVENTS: Dict[State, Set[EventType]] = {
    State.PREMATCH: {EventType.SIGNAL_GENERATED},
    State.ACTIVE: {EventType.ODDS_UPDATED, EventType.SIGNAL_GENERATED},
    State.CLOSED: {EventType.MATCH_FINISHED, EventType.RESULT_CONFIRMED},
}

# idempotent self-deliveries: re-sending a milestone while already in its target
# state is a no-op, not an error.
_IDEMPOTENT_SELF: Set[Tuple[State, EventType]] = {
    (State.ACTIVE, EventType.ODDS_UPDATED),   # handled as data; explicit for clarity
    (State.LOCKED, EventType.MARKET_LOCKED),
    (State.CLOSED, EventType.MATCH_STARTED),
    (State.SETTLED, EventType.SETTLEMENT_COMPLETED),
    (State.VOID, EventType.MATCH_VOIDED),
    (State.CANCELLED, EventType.MATCH_CANCELLED),
}

# allowed orchestration actions per state (used by the control plane, M17)
ALLOWED_ACTIONS: Dict[State, Set[str]] = {
    State.PREMATCH: {"ingest_odds", "generate_signal"},
    State.ACTIVE: {"ingest_odds", "generate_signal"},
    State.LOCKED: {"capture_close"},
    State.CLOSED: {"record_result"},
    State.SETTLED: set(),
    State.VOID: set(),
    State.CANCELLED: set(),
}


@dataclass
class Event:
    match_id: str
    type: EventType
    idempotency_key: str
    occurred_at: datetime
    payload: dict = field(default_factory=dict)
    # set by the store on append (append-only audit)
    seq: Optional[int] = None
    recorded_at: Optional[datetime] = None

    def __post_init__(self):
        if isinstance(self.type, str):
            self.type = EventType(self.type)
        if self.occurred_at.tzinfo is None:
            self.occurred_at = self.occurred_at.replace(tzinfo=timezone.utc)


@dataclass
class ApplyResult:
    outcome: Outcome
    state: Optional[State]
    reason: str = ""


@dataclass
class MatchLifecycle:
    """Event-sourced aggregate for one match. Deterministic fold of events."""

    match_id: str
    state: Optional[State] = None
    event_count: int = 0
    transition_count: int = 0
    invalid_transition_count: int = 0
    duplicate_event_count: int = 0
    last_transition_at: Optional[datetime] = None
    seen_keys: Set[str] = field(default_factory=set)
    # CLOSED-phase milestones (guards for settlement)
    started: bool = False
    finished: bool = False
    result_confirmed: bool = False

    # -- apply one event ---------------------------------------------------
    def apply(self, ev: Event) -> ApplyResult:
        if ev.match_id != self.match_id:
            return ApplyResult(Outcome.INVALID, self.state, "match_id mismatch")

        # 1. idempotency: duplicate key -> no-op
        if ev.idempotency_key in self.seen_keys:
            self.duplicate_event_count += 1
            return ApplyResult(Outcome.DUPLICATE, self.state, "duplicate idempotency_key")
        self.seen_keys.add(ev.idempotency_key)
        self.event_count += 1

        # 2. idempotent self-delivery of a milestone (already in target state):
        #    checked before guards so re-delivering a completed milestone is a
        #    safe no-op rather than a guard failure.
        if (self.state, ev.type) in _IDEMPOTENT_SELF:
            self._record_milestone(ev.type)
            return ApplyResult(Outcome.IDEMPOTENT, self.state, "self-delivery no-op")

        # 3. guard checks (settlement ordering etc.)
        guard = self._guard(ev.type)
        if guard is not None and not guard():
            self.invalid_transition_count += 1
            return ApplyResult(Outcome.INVALID, self.state, "guard failed")

        # 4. state-changing transition
        target = _BASE_TRANSITIONS.get((self.state, ev.type))
        if target is not None:
            self.state = target
            self.transition_count += 1
            self.last_transition_at = ev.occurred_at
            self._record_milestone(ev.type)
            return ApplyResult(Outcome.APPLIED, self.state, "transition")

        # 5. data event allowed in this state (no transition)
        if self.state is not None and ev.type in _DATA_EVENTS.get(self.state, set()):
            self._record_milestone(ev.type)
            return ApplyResult(Outcome.IDEMPOTENT, self.state, "data event")

        # 6. otherwise illegal
        self.invalid_transition_count += 1
        return ApplyResult(Outcome.INVALID, self.state,
                           f"illegal {ev.type.value} from {self.state}")

    # -- guards ------------------------------------------------------------
    def _guard(self, etype: EventType) -> Optional[Callable[[], bool]]:
        if etype == EventType.RESULT_CONFIRMED:
            return lambda: self.state == State.CLOSED and self.finished
        if etype == EventType.SETTLEMENT_COMPLETED:
            return lambda: self.state == State.CLOSED and self.result_confirmed
        if etype == EventType.MATCH_FINISHED:
            return lambda: self.state == State.CLOSED and self.started
        return None

    def _record_milestone(self, etype: EventType) -> None:
        if etype == EventType.MATCH_STARTED:
            self.started = True
        elif etype == EventType.MATCH_FINISHED:
            self.finished = True
        elif etype == EventType.RESULT_CONFIRMED:
            self.result_confirmed = True

    # -- observability -----------------------------------------------------
    def state_age(self, now: Optional[datetime] = None) -> Optional[float]:
        if self.last_transition_at is None:
            return None
        now = now or datetime.now(timezone.utc)
        return (now - self.last_transition_at).total_seconds()

    def allowed_actions(self) -> Set[str]:
        return ALLOWED_ACTIONS.get(self.state, set()) if self.state else set()

    def can_generate_signal(self) -> bool:
        return "generate_signal" in self.allowed_actions()

    def can_ingest_odds(self) -> bool:
        return "ingest_odds" in self.allowed_actions()

    def is_terminal(self) -> bool:
        return self.state in TERMINAL_STATES

    def snapshot(self, now: Optional[datetime] = None) -> dict:
        return {
            "match_id": self.match_id,
            "current_state": self.state.value if self.state else None,
            "state_age": self.state_age(now),
            "event_count": self.event_count,
            "transition_count": self.transition_count,
            "invalid_transition_count": self.invalid_transition_count,
            "duplicate_event_count": self.duplicate_event_count,
            "terminal": self.is_terminal(),
        }

    # -- deterministic reconstruction --------------------------------------
    @classmethod
    def replay(cls, match_id: str, events: List[Event]) -> "MatchLifecycle":
        agg = cls(match_id)
        for ev in events:
            agg.apply(ev)
        return agg


def is_legal_transition(state: Optional[State], etype: EventType) -> bool:
    """True if the event would be accepted (transition, data, or idempotent)."""
    if (state, etype) in _BASE_TRANSITIONS:
        return True
    if (state, etype) in _IDEMPOTENT_SELF:
        return True
    if state is not None and etype in _DATA_EVENTS.get(state, set()):
        return True
    return False
