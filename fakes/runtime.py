import uuid
from collections.abc import Sequence

from contracts.runtime import (
    Command,
    CommandStatus,
    CommandStatusEvent,
    RunEvent,
    UnknownCommandError,
)


class InMemoryRunLog:
    def __init__(self) -> None:
        self._events: list[RunEvent] = []

    def append(self, event: RunEvent) -> None:
        self._events.append(event)

    def latest(self) -> RunEvent | None:
        return self._events[-1] if self._events else None

    def events(self, run_id: uuid.UUID) -> Sequence[RunEvent]:
        return [e for e in self._events if e.run_id == run_id]


class InMemoryCommandInbox:
    def __init__(self) -> None:
        self._commands: dict[uuid.UUID, Command] = {}
        self._history: dict[uuid.UUID, list[CommandStatusEvent]] = {}

    def submit(self, command: Command) -> None:
        if command.id in self._commands:
            raise ValueError(f"command {command.id} already submitted")
        self._commands[command.id] = command
        self._history[command.id] = [
            CommandStatusEvent(
                event_id=uuid.uuid4(),
                command_id=command.id,
                at=command.created_at,
                status=CommandStatus.PENDING,
            )
        ]

    def pending(self) -> Sequence[Command]:
        return [
            self._commands[command_id]
            for command_id, events in self._history.items()
            if events[-1].status is CommandStatus.PENDING
        ]

    def record_status(self, event: CommandStatusEvent) -> None:
        if event.command_id not in self._commands:
            raise UnknownCommandError(str(event.command_id))
        self._history[event.command_id].append(event)

    def history(self, command_id: uuid.UUID) -> Sequence[CommandStatusEvent]:
        return list(self._history.get(command_id, []))
