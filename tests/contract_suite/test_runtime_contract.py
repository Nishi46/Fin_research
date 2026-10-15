import uuid
from datetime import timedelta

import pytest

from contracts.runtime import (
    Command,
    CommandInbox,
    CommandKind,
    CommandStatus,
    CommandStatusEvent,
    RunEvent,
    RunLog,
    RunStatus,
    UnknownCommandError,
)
from tests.helpers import T0


def run_event(run_id: uuid.UUID, status: RunStatus, minutes: int = 0) -> RunEvent:
    return RunEvent(
        event_id=uuid.uuid4(),
        run_id=run_id,
        at=T0 + timedelta(minutes=minutes),
        status=status,
        host="local",
    )


def command(kind: CommandKind = CommandKind.SET_CAPITAL) -> Command:
    return Command(
        id=uuid.uuid4(),
        created_at=T0,
        kind=kind,
        payload={"amount": "25000"},
        issued_by="owner",
    )


def status_event(command_id: uuid.UUID, status: CommandStatus, minutes: int) -> CommandStatusEvent:
    return CommandStatusEvent(
        event_id=uuid.uuid4(),
        command_id=command_id,
        at=T0 + timedelta(minutes=minutes),
        status=status,
    )


def test_runlog_latest_is_none_when_empty(runlog: RunLog) -> None:
    assert runlog.latest() is None


def test_runlog_latest_is_last_appended(runlog: RunLog) -> None:
    run_id = uuid.uuid4()
    runlog.append(run_event(run_id, RunStatus.STARTED, 0))
    last = run_event(run_id, RunStatus.SUCCEEDED, 1)
    runlog.append(last)
    assert runlog.latest() == last


def test_runlog_events_are_per_run_and_ordered(runlog: RunLog) -> None:
    a, b = uuid.uuid4(), uuid.uuid4()
    runlog.append(run_event(a, RunStatus.STARTED, 0))
    runlog.append(run_event(b, RunStatus.STARTED, 1))
    runlog.append(run_event(a, RunStatus.FAILED, 2))
    assert [e.status for e in runlog.events(a)] == [RunStatus.STARTED, RunStatus.FAILED]
    assert len(runlog.events(b)) == 1


def test_submit_records_pending_status(inbox: CommandInbox) -> None:
    cmd = command()
    inbox.submit(cmd)
    assert [c.id for c in inbox.pending()] == [cmd.id]
    assert [e.status for e in inbox.history(cmd.id)] == [CommandStatus.PENDING]


def test_applied_and_rejected_commands_leave_pending(inbox: CommandInbox) -> None:
    applied, rejected, waiting = command(), command(CommandKind.PROMOTE), command()
    for c in (applied, rejected, waiting):
        inbox.submit(c)
    inbox.record_status(status_event(applied.id, CommandStatus.APPLIED, 1))
    inbox.record_status(status_event(rejected.id, CommandStatus.REJECTED, 1))
    assert [c.id for c in inbox.pending()] == [waiting.id]


def test_history_is_append_only_and_ordered(inbox: CommandInbox) -> None:
    cmd = command()
    inbox.submit(cmd)
    inbox.record_status(status_event(cmd.id, CommandStatus.REJECTED, 1))
    assert [e.status for e in inbox.history(cmd.id)] == [
        CommandStatus.PENDING,
        CommandStatus.REJECTED,
    ]


def test_status_for_unknown_command_is_rejected(inbox: CommandInbox) -> None:
    with pytest.raises(UnknownCommandError):
        inbox.record_status(status_event(uuid.uuid4(), CommandStatus.APPLIED, 1))
