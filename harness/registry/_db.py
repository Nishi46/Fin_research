"""Shared SQLite plumbing: opens a database and applies the frozen DDL unchanged."""

import sqlite3
from datetime import datetime
from pathlib import Path

import contracts

# Resolved from the package location so no copy of the DDL exists in the harness.
DDL_PATH = Path(contracts.__file__).resolve().parent / "sql" / "registry_sqlite.sql"


def connect(database: str) -> sqlite3.Connection:
    """Open `database` (":memory:" for tests) with the frozen schema applied if it is new."""
    conn = sqlite3.connect(database, isolation_level=None)
    has_schema = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'trials'"
    ).fetchone()
    if has_schema is None:
        conn.executescript(DDL_PATH.read_text(encoding="utf-8"))
    return conn


def to_text(ts: datetime) -> str:
    return ts.isoformat()


def from_text(text: str) -> datetime:
    return datetime.fromisoformat(text)
