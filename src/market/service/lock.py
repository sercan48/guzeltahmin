"""PHASE-LIVE L3 — Single-instance file lock (stdlib, Linux/macOS)."""

from __future__ import annotations

import fcntl
import os


class SingleInstanceLock:
    """Exclusive advisory lock backed by a PID file.

    Uses non-blocking flock so the caller can decide to abort or wait.
    """

    def __init__(self, path: str) -> None:
        self._path = path
        self._fd = None

    def acquire(self) -> bool:
        """Return True if the lock was acquired, False if already held."""
        try:
            self._fd = open(self._path, "w")
            fcntl.flock(self._fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self._fd.write(str(os.getpid()))
            self._fd.flush()
            return True
        except OSError:
            if self._fd is not None:
                try:
                    self._fd.close()
                except OSError:
                    pass
            self._fd = None
            return False

    def release(self) -> None:
        if self._fd is not None:
            try:
                fcntl.flock(self._fd, fcntl.LOCK_UN)
                self._fd.close()
            except OSError:
                pass
            self._fd = None

    def __enter__(self) -> "SingleInstanceLock":
        if not self.acquire():
            raise RuntimeError(
                f"Another MIW service instance is running (lock: {self._path}). "
                "Stop it before starting a new one."
            )
        return self

    def __exit__(self, *_) -> None:
        self.release()
