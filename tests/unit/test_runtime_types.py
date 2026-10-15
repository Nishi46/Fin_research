import uuid
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from contracts.runtime import (
    Command,
    CommandInbox,
    CommandKind,
    CommandStatus,
    CommandStatusEvent,
    RunEvent,
    RunLog,
    RunStatus,
)

NOW = datetime(2026, 1, 5, 21, 0, tzinfo=UTC)
FORBIDDEN = {"update", "delete", "remove", "pop", "clear", "upsert", "replace", "truncate"}


def test_command_is_immutable() -> None:
    cmd = Command(
        id=uuid.uuid4(),
        created_at=NOW,
        kind=CommandKind.SET_CAPITAL,
        payload={"amount": "25000"},
        issued_by="owner",
    )
    with pytest.raises(ValidationError):
        cmd.issued_by = "someone-else"  # type: ignore[misc]


def test_status_values() -> None:
    assert {s.value for s in CommandStatus} == {"pending", "applied", "rejected"}
    assert {s.value for s in RunStatus} == {"started", "succeeded", "failed"}
    assert {k.value for k in CommandKind} == {
        "set_capital",
        "promote",
        "reject",
        "set_threshold",
    }


def test_events_reject_naive_timestamps() -> None:
    with pytest.raises(ValidationError):
        RunEvent(
            event_id=uuid.uuid4(),
            run_id=uuid.uuid4(),
            at=datetime(2026, 1, 5),
            status=RunStatus.STARTED,
            host="laptop",
        )
    with pytest.raises(ValidationError):
        CommandStatusEvent(
            event_id=uuid.uuid4(),
            command_id=uuid.uuid4(),
            at=datetime(2026, 1, 5),
            status=CommandStatus.PENDING,
        )


@pytest.mark.parametrize("proto", [RunLog, CommandInbox])
def test_protocols_expose_no_mutation_methods(proto: type) -> None:
    public = {n for n in dir(proto) if not n.startswith("_")}
    assert public.isdisjoint(FORBIDDEN), public & FORBIDDEN


def test_command_payload_cannot_be_mutated() -> None:
    cmd = Command(
        id=uuid.uuid4(),
        created_at=NOW,
        kind=CommandKind.SET_CAPITAL,
        payload={"amount": "25000"},
        issued_by="owner",
    )
    with pytest.raises(TypeError):
        cmd.payload["amount"] = "9999999"  # type: ignore[index]


def test_command_survives_pickle_and_deepcopy() -> None:
    import copy
    import pickle

    cmd = Command(
        id=uuid.uuid4(),
        created_at=NOW,
        kind=CommandKind.PROMOTE,
        payload={"candidate": "abc"},
        issued_by="owner",
    )
    assert pickle.loads(pickle.dumps(cmd)) == cmd
    assert copy.deepcopy(cmd) == cmd
