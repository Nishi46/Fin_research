import uuid
from collections.abc import Sequence
from enum import StrEnum
from typing import Protocol

from pydantic import BaseModel, ConfigDict

from contracts._frozen import FrozenJsonMap
from contracts._time import UtcDatetime


class UnknownCommandError(KeyError):
    """Raised when a status is recorded for a command that was never submitted."""


class RunStatus(StrEnum):
    STARTED = "started"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class RunEvent(BaseModel):
    model_config = ConfigDict(frozen=True)

    event_id: uuid.UUID
    run_id: uuid.UUID
    at: UtcDatetime
    status: RunStatus
    host: str
    detail: str | None = None


class RunLog(Protocol):
    """Append-only engine heartbeat. The console flags a stale `latest()`."""

    def append(self, event: RunEvent) -> None: ...

    def latest(self) -> RunEvent | None: ...

    def events(self, run_id: uuid.UUID) -> Sequence[RunEvent]: ...


class CommandKind(StrEnum):
    SET_CAPITAL = "set_capital"
    PROMOTE = "promote"
    REJECT = "reject"
    SET_THRESHOLD = "set_threshold"


class CommandStatus(StrEnum):
    PENDING = "pending"
    APPLIED = "applied"
    REJECTED = "rejected"


class Command(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: uuid.UUID
    created_at: UtcDatetime
    kind: CommandKind
    payload: FrozenJsonMap
    issued_by: str


class CommandStatusEvent(BaseModel):
    model_config = ConfigDict(frozen=True)

    event_id: uuid.UUID
    command_id: uuid.UUID
    at: UtcDatetime
    status: CommandStatus
    reason: str | None = None


class CommandInbox(Protocol):
    """Website-to-engine commands. Status only ever moves by appending history.

    `submit` also records the initial PENDING status. The engine applies a command only
    after validating it against the risk layer's hard caps, and may reject it.
    """

    def submit(self, command: Command) -> None: ...

    def pending(self) -> Sequence[Command]: ...

    def record_status(self, event: CommandStatusEvent) -> None: ...

    def history(self, command_id: uuid.UUID) -> Sequence[CommandStatusEvent]: ...
