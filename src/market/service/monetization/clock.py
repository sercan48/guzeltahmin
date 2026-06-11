"""Clock abstraction for deterministic time-dependent behaviour.

Inject ManualClock in tests; SystemClock in production.
No wall-clock calls anywhere except through this interface.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime, timezone


class Clock(ABC):
    @abstractmethod
    def now_ts(self) -> float:
        """Current time as Unix float (UTC)."""

    @abstractmethod
    def now_iso(self) -> str:
        """Current time as ISO-8601 string (UTC)."""

    @abstractmethod
    def today_str(self) -> str:
        """Current date as YYYY-MM-DD (UTC)."""

    @abstractmethod
    def week_str(self) -> str:
        """Current ISO week as YYYY-WW (UTC)."""


class SystemClock(Clock):
    def now_ts(self) -> float:
        return datetime.now(timezone.utc).timestamp()

    def now_iso(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def today_str(self) -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")

    def week_str(self) -> str:
        d = datetime.now(timezone.utc)
        return f"{d.year}-{d.isocalendar()[1]:02d}"


class ManualClock(Clock):
    """Deterministic clock for testing and replay verification."""

    def __init__(self, ts: float = 0.0) -> None:
        self._ts = ts

    def advance(self, seconds: float) -> None:
        self._ts += seconds

    def set_ts(self, ts: float) -> None:
        self._ts = ts

    def now_ts(self) -> float:
        return self._ts

    def now_iso(self) -> str:
        return datetime.fromtimestamp(self._ts, tz=timezone.utc).isoformat()

    def today_str(self) -> str:
        return datetime.fromtimestamp(self._ts, tz=timezone.utc).strftime("%Y-%m-%d")

    def week_str(self) -> str:
        d = datetime.fromtimestamp(self._ts, tz=timezone.utc)
        return f"{d.year}-{d.isocalendar()[1]:02d}"
