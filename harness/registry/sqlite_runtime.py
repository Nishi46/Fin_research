"""SQLite `RunLog` and `CommandInbox` on the frozen append-only DDL."""

import json
import sqlite3
import uuid
from collections.abc import Sequence

from contracts.runtime import (
    Command,
    CommandKind,
    CommandStatus,
    CommandStatusEvent,
    RunEvent,
    RunStatus,
    UnknownCommandError,
)
from harness.registry._db import connect, from_text, to_text

_RUN_COLUMNS = "event_id, run_id, at, status, host, detail"
_STATUS_COLUMNS = "event_id, command_id, at, status, reason"


class SqliteRunLog:
    def __init__(self, database: str = ":memory:") -> None:
        self._conn = connect(database)

    @property
    def connection(self) -> sqlite3.Connection:
        return self._conn

    def append(self, event: RunEvent) -> None:
        self._conn.execute(
            f"INSERT INTO runs ({_RUN_COLUMNS}) VALUES (?,?,?,?,?,?)",
            (
                str(event.event_id),
                str(event.run_id),
                to_text(event.at),
                event.status.value,
                event.host,
                event.detail,
            ),
        )

    def latest(self) -> RunEvent | None:
        row = self._conn.execute(
            f"SELECT {_RUN_COLUMNS} FROM runs ORDER BY rowid DESC LIMIT 1"
        ).fetchone()
        return None if row is None else _to_event(row)

    def events(self, run_id: uuid.UUID) -> Sequence[RunEvent]:
        rows = self._conn.execute(
            f"SELECT {_RUN_COLUMNS} FROM runs WHERE run_id = ? ORDER BY rowid", (str(run_id),)
        )
        return [_to_event(row) for row in rows]


class SqliteCommandInbox:
    def __init__(self, database: str = ":memory:") -> None:
        self._conn = connect(database)

    @property
    def connection(self) -> sqlite3.Connection:
        return self._conn

    def submit(self, command: Command) -> None:
        # The command and its PENDING status are written together or not at all.
        self._conn.execute("BEGIN")
        try:
            self._conn.execute(
                "INSERT INTO commands (id, created_at, kind, payload, issued_by)"
                " VALUES (?,?,?,?,?)",
                (
                    str(command.id),
                    to_text(command.created_at),
                    command.kind.value,
                    json.dumps(dict(command.payload)),
                    command.issued_by,
                ),
            )
            self._insert_status(
                CommandStatusEvent(
                    event_id=uuid.uuid4(),
                    command_id=command.id,
                    at=command.created_at,
                    status=CommandStatus.PENDING,
                )
            )
            self._conn.execute("COMMIT")
        except sqlite3.IntegrityError as err:
            self._conn.execute("ROLLBACK")
            raise ValueError(f"command {command.id} already submitted") from err
        except BaseException:
            self._conn.execute("ROLLBACK")
            raise

    def pending(self) -> Sequence[Command]:
        rows = self._conn.execute(
            "SELECT c.id, c.created_at, c.kind, c.payload, c.issued_by FROM commands c"
            " WHERE (SELECT h.status FROM command_status_history h"
            "        WHERE h.command_id = c.id ORDER BY h.rowid DESC LIMIT 1) = 'pending'"
            " ORDER BY c.rowid"
        )
        return [
            Command(
                id=uuid.UUID(str(r[0])),
                created_at=from_text(str(r[1])),
                kind=CommandKind(str(r[2])),
                payload=json.loads(str(r[3])),
                issued_by=str(r[4]),
            )
            for r in rows
        ]

    def record_status(self, event: CommandStatusEvent) -> None:
        known = self._conn.execute(
            "SELECT 1 FROM commands WHERE id = ?", (str(event.command_id),)
        ).fetchone()
        if known is None:
            raise UnknownCommandError(str(event.command_id))
        self._insert_status(event)

    def history(self, command_id: uuid.UUID) -> Sequence[CommandStatusEvent]:
        rows = self._conn.execute(
            f"SELECT {_STATUS_COLUMNS} FROM command_status_history"
            " WHERE command_id = ? ORDER BY rowid",
            (str(command_id),),
        )
        return [
            CommandStatusEvent(
                event_id=uuid.UUID(str(r[0])),
                command_id=uuid.UUID(str(r[1])),
                at=from_text(str(r[2])),
                status=CommandStatus(str(r[3])),
                reason=None if r[4] is None else str(r[4]),
            )
            for r in rows
        ]

    def _insert_status(self, event: CommandStatusEvent) -> None:
        self._conn.execute(
            f"INSERT INTO command_status_history ({_STATUS_COLUMNS}) VALUES (?,?,?,?,?)",
            (
                str(event.event_id),
                str(event.command_id),
                to_text(event.at),
                event.status.value,
                event.reason,
            ),
        )


def _to_event(row: tuple[object, ...]) -> RunEvent:
    return RunEvent(
        event_id=uuid.UUID(str(row[0])),
        run_id=uuid.UUID(str(row[1])),
        at=from_text(str(row[2])),
        status=RunStatus(str(row[3])),
        host=str(row[4]),
        detail=None if row[5] is None else str(row[5]),
    )
