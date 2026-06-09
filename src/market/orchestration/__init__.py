"""M4 — Market lifecycle orchestration backbone (deterministic, event-sourced).

Orchestration only; no prediction logic.
"""

from .lifecycle import (
    State,
    EventType,
    Outcome,
    Event,
    ApplyResult,
    MatchLifecycle,
    TERMINAL_STATES,
    ALLOWED_ACTIONS,
    is_legal_transition,
)
from .event_store import EventStore, LifecycleService

__all__ = [
    "State",
    "EventType",
    "Outcome",
    "Event",
    "ApplyResult",
    "MatchLifecycle",
    "TERMINAL_STATES",
    "ALLOWED_ACTIONS",
    "is_legal_transition",
    "EventStore",
    "LifecycleService",
]
