"""M10.1 — Injectable clock.

The scheduler reads time only through a Clock, never wall-clock directly, so
scheduling is deterministic and replay-safe. Tests use ManualClock; production
would inject SystemClock.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime, timedelta, timezone


class Clock(ABC):
    @abstractmethod
    def now(self) -> datetime:
        ...


class ManualClock(Clock):
    """Logical clock advanced explicitly — deterministic, wall-clock independent."""

    def __init__(self, t: datetime) -> None:
        self._t = self._utc(t)

    def now(self) -> datetime:
        return self._t

    def set(self, t: datetime) -> None:
        self._t = self._utc(t)

    def advance(self, **kwargs) -> None:
        self._t = self._t + timedelta(**kwargs)

    @staticmethod
    def _utc(t: datetime) -> datetime:
        return t.replace(tzinfo=timezone.utc) if t.tzinfo is None else t.astimezone(timezone.utc)


class SystemClock(Clock):
    """Wall-clock (production only; not used in deterministic replay/tests)."""

    def now(self) -> datetime:
        return datetime.now(timezone.utc)
