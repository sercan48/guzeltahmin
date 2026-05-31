"""SQLite database backend implementation."""

import sqlite3
from pathlib import Path
from typing import Any

from src.db.base import DatabaseBackend


class SQLiteBackend(DatabaseBackend):
    """SQLite implementation — zero-config, portable, great for dev."""

    def __init__(self, db_path: Path | None = None):
        from config.settings import SQLITE_PATH
        self.db_path = db_path or SQLITE_PATH
        self.conn: sqlite3.Connection | None = None

    def connect(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")

    def close(self) -> None:
        if self.conn:
            self.conn.close()
            self.conn = None

    def execute(self, query: str, params: tuple = ()) -> Any:
        cursor = self.conn.execute(query, params)
        self.conn.commit()
        return cursor

    def executemany(self, query: str, params_list: list[tuple]) -> None:
        self.conn.executemany(query, params_list)
        self.conn.commit()

    def fetchall(self, query: str, params: tuple = ()) -> list[dict]:
        cursor = self.conn.execute(query, params)
        rows = cursor.fetchall()
        return [dict(row) for row in rows]

    def fetchone(self, query: str, params: tuple = ()) -> dict | None:
        cursor = self.conn.execute(query, params)
        row = cursor.fetchone()
        return dict(row) if row else None

    def table_exists(self, table_name: str) -> bool:
        result = self.fetchone(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (table_name,),
        )
        return result is not None

    def get_row_count(self, table_name: str) -> int:
        result = self.fetchone(f"SELECT COUNT(*) as cnt FROM {table_name}")
        return result["cnt"] if result else 0
