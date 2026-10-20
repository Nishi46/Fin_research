"""Shared SQLite plumbing: opens a database and applies the frozen DDL unchanged."""

import sqlite3
from datetime import datetime
from pathlib import Path

import contracts

# Resolved from the package location so no copy of the DDL exists in the harness.
DDL_PATH = Path(contracts.__file__).resolve().parent / "sql" / "registry_sqlite.sql"


def connect(database: str) -> sqlite3.Connection:
    """Open `database` (":memory:" for tests) and apply the frozen schema.

    The DDL is idempotent, so applying it to an existing file only adds what is missing.
    """
    conn = sqlite3.connect(database, isolation_level=None)
    conn.executescript(DDL_PATH.read_text(encoding="utf-8"))
    return conn


def to_text(ts: datetime) -> str:
    return ts.isoformat()


def from_text(text: str) -> datetime:
    return datetime.fromisoformat(text)
