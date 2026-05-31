"""Abstract database interface for backend portability."""

from abc import ABC, abstractmethod
from typing import Any


class DatabaseBackend(ABC):
    """Abstract base class for database backends.

    Implementations: SQLiteBackend, SupabaseBackend
    Switching backends requires only changing DB_BACKEND in .env
    """

    @abstractmethod
    def connect(self) -> None:
        """Establish database connection."""

    @abstractmethod
    def close(self) -> None:
        """Close database connection."""

    @abstractmethod
    def execute(self, query: str, params: tuple = ()) -> Any:
        """Execute a single SQL query."""

    @abstractmethod
    def executemany(self, query: str, params_list: list[tuple]) -> None:
        """Execute a query with multiple parameter sets (batch insert)."""

    @abstractmethod
    def fetchall(self, query: str, params: tuple = ()) -> list[dict]:
        """Execute query and return all rows as dicts."""

    @abstractmethod
    def fetchone(self, query: str, params: tuple = ()) -> dict | None:
        """Execute query and return first row as dict."""

    @abstractmethod
    def table_exists(self, table_name: str) -> bool:
        """Check if a table exists."""

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()


def get_backend() -> DatabaseBackend:
    """Factory: return the configured database backend."""
    from config.settings import DB_BACKEND

    if DB_BACKEND == "sqlite":
        from src.db.sqlite_backend import SQLiteBackend
        return SQLiteBackend()
    elif DB_BACKEND == "supabase":
        from src.db.supabase_backend import SupabaseBackend
        return SupabaseBackend()
    else:
        raise ValueError(f"Unknown DB_BACKEND: {DB_BACKEND}")
