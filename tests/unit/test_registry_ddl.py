import re
import sqlite3
import uuid
from pathlib import Path

import pytest

from contracts.registry import Outcome, Stage
from contracts.runtime import CommandKind, CommandStatus, RunStatus

SQL_DIR = Path(__file__).resolve().parents[2] / "contracts" / "sql"
TABLES = ["trials", "runs", "commands", "command_status_history"]


def sqlite_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.executescript((SQL_DIR / "registry_sqlite.sql").read_text())
    return conn


def insert_trial(
    conn: sqlite3.Connection, stage: str = "lookahead", outcome: str = "rejected"
) -> str:
    tid = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO trials (id, created_at, strategy_id, code_hash, params, capital_amount,"
        " capital_currency, data_snapshot_id, survivorship_biased, stage_reached, outcome,"
        " metrics) VALUES (?, '2026-01-05T00:00:00Z', 's', 'h', '{}', '10000', 'USD', 'snap',"
        " 1, ?, ?, '{}')",
        (tid, stage, outcome),
    )
    return tid


def insert_run(conn: sqlite3.Connection, status: str = "started") -> str:
    eid = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO runs (event_id, run_id, at, status, host, detail)"
        " VALUES (?, ?, '2026-01-05T00:00:00Z', ?, 'laptop', NULL)",
        (eid, str(uuid.uuid4()), status),
    )
    return eid


def insert_command(conn: sqlite3.Connection, kind: str = "set_capital") -> str:
    cid = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO commands (id, created_at, kind, payload, issued_by)"
        " VALUES (?, '2026-01-05T00:00:00Z', ?, '{}', 'owner')",
        (cid, kind),
    )
    return cid


def insert_history(conn: sqlite3.Connection, status: str = "pending") -> str:
    eid = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO command_status_history (event_id, command_id, at, status, reason)"
        " VALUES (?, ?, '2026-01-05T00:00:00Z', ?, NULL)",
        (eid, str(uuid.uuid4()), status),
    )
    return eid


def test_insert_works_on_every_table() -> None:
    conn = sqlite_db()
    insert_trial(conn)
    insert_run(conn)
    insert_command(conn)
    insert_history(conn)
    for table in TABLES:
        assert conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 1


@pytest.mark.parametrize("table", TABLES)
def test_update_and_delete_are_blocked(table: str) -> None:
    conn = sqlite_db()
    insert_trial(conn)
    insert_run(conn)
    insert_command(conn)
    insert_history(conn)
    first_column = conn.execute(
        f"SELECT name FROM pragma_table_info('{table}') LIMIT 1"
    ).fetchone()[0]
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        conn.execute(f"UPDATE {table} SET {first_column} = {first_column}")
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        conn.execute(f"DELETE FROM {table}")


def test_check_constraints_match_enums() -> None:
    conn = sqlite_db()
    for stage in Stage:
        insert_trial(conn, stage=stage.value)
    for outcome in Outcome:
        insert_trial(conn, outcome=outcome.value)
    for status in RunStatus:
        insert_run(conn, status=status.value)
    for kind in CommandKind:
        insert_command(conn, kind=kind.value)
    for cmd_status in CommandStatus:
        insert_history(conn, status=cmd_status.value)
    with pytest.raises(sqlite3.IntegrityError):
        insert_trial(conn, stage="not_a_stage")
    with pytest.raises(sqlite3.IntegrityError):
        insert_trial(conn, outcome="not_an_outcome")
    with pytest.raises(sqlite3.IntegrityError):
        insert_run(conn, status="nope")
    with pytest.raises(sqlite3.IntegrityError):
        insert_command(conn, kind="nope")
    with pytest.raises(sqlite3.IntegrityError):
        insert_history(conn, status="nope")


def test_postgres_ddl_follows_schema_rules() -> None:
    sql = (SQL_DIR / "registry_postgres.sql").read_text()
    lowered = sql.lower()
    for table in TABLES:
        assert f"create table {table}" in lowered
        assert re.search(rf"before update or delete or truncate on {table}\b", lowered), table
    assert "uuid_generate_v4()" in lowered
    assert "gen_random_uuid" not in lowered
    assert "timestamptz" in lowered
    assert "varchar" not in lowered
    assert "references" not in lowered
    assert "on delete cascade" not in lowered
    assert "append-only" in lowered


def test_sqlite_ddl_has_no_forbidden_constructs() -> None:
    lowered = (SQL_DIR / "registry_sqlite.sql").read_text().lower()
    assert "varchar" not in lowered
    assert "references" not in lowered


def test_insert_or_replace_cannot_rewrite_a_trial() -> None:
    conn = sqlite_db()
    tid = insert_trial(conn)
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        conn.execute(
            "INSERT OR REPLACE INTO trials (id, created_at, strategy_id, code_hash, params,"
            " capital_amount, capital_currency, data_snapshot_id, survivorship_biased,"
            " stage_reached, outcome, metrics) VALUES (?, '2026-01-06T00:00:00Z', 'TAMPERED',"
            " 'h', '{}', '1', 'USD', 'snap', 0, 'owner_gate', 'passed', '{}')",
            (tid,),
        )
    assert conn.execute("SELECT strategy_id FROM trials WHERE id = ?", (tid,)).fetchone() == ("s",)


def test_upsert_cannot_rewrite_a_trial() -> None:
    conn = sqlite_db()
    tid = insert_trial(conn)
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        conn.execute(
            "INSERT INTO trials (id, created_at, strategy_id, code_hash, params, capital_amount,"
            " capital_currency, data_snapshot_id, survivorship_biased, stage_reached, outcome,"
            " metrics) VALUES (?, '2026-01-06T00:00:00Z', 'TAMPERED', 'h', '{}', '1', 'USD',"
            " 'snap', 0, 'owner_gate', 'passed', '{}')"
            " ON CONFLICT(id) DO UPDATE SET strategy_id = excluded.strategy_id",
            (tid,),
        )
    assert conn.execute("SELECT strategy_id FROM trials WHERE id = ?", (tid,)).fetchone() == ("s",)


@pytest.mark.parametrize(
    ("table", "key"),
    [
        ("runs", "event_id"),
        ("commands", "id"),
        ("command_status_history", "event_id"),
    ],
)
def test_replace_is_blocked_on_every_table(table: str, key: str) -> None:
    conn = sqlite_db()
    existing = {
        "runs": insert_run,
        "commands": insert_command,
        "command_status_history": insert_history,
    }[table](conn)
    columns = [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]
    row = conn.execute(f"SELECT * FROM {table} WHERE {key} = ?", (existing,)).fetchone()
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        conn.execute(
            f"INSERT OR REPLACE INTO {table} ({', '.join(columns)})"
            f" VALUES ({', '.join('?' for _ in columns)})",
            row,
        )
