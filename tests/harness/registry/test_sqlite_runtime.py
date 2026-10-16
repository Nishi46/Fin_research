import sqlite3
import uuid

import pytest

from contracts.runtime import CommandStatus, RunStatus
from harness.registry.sqlite_runtime import SqliteCommandInbox, SqliteRunLog
from tests.contract_suite.test_runtime_contract import command, run_event, status_event


def test_runlog_rows_cannot_be_updated_or_deleted() -> None:
    log = SqliteRunLog()
    log.append(run_event(uuid.uuid4(), RunStatus.STARTED))
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        log.connection.execute("UPDATE runs SET status = 'failed'")
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        log.connection.execute("DELETE FROM runs")
    assert log.latest() is not None


def test_runlog_round_trips_detail() -> None:
    log = SqliteRunLog()
    event = run_event(uuid.uuid4(), RunStatus.FAILED).model_copy(update={"detail": "boom"})
    log.append(event)
    assert log.latest() == event


def test_duplicate_command_is_rejected() -> None:
    inbox = SqliteCommandInbox()
    cmd = command()
    inbox.submit(cmd)
    with pytest.raises(ValueError, match="already submitted"):
        inbox.submit(cmd)
    assert len(inbox.history(cmd.id)) == 1


def test_command_history_cannot_be_rewritten() -> None:
    inbox = SqliteCommandInbox()
    cmd = command()
    inbox.submit(cmd)
    inbox.record_status(status_event(cmd.id, CommandStatus.APPLIED, 1))
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        inbox.connection.execute("DELETE FROM command_status_history")
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        inbox.connection.execute("UPDATE commands SET issued_by = 'x'")
    assert inbox.pending() == []


def test_command_round_trips() -> None:
    inbox = SqliteCommandInbox()
    cmd = command()
    inbox.submit(cmd)
    assert inbox.pending() == [cmd]
