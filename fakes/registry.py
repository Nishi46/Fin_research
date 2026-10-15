import uuid
from collections.abc import Sequence

from contracts.registry import DuplicateTrialError, Trial


class InMemoryRegistry:
    def __init__(self) -> None:
        self._trials: dict[uuid.UUID, Trial] = {}

    def append(self, trial: Trial) -> None:
        if trial.id in self._trials:
            raise DuplicateTrialError(str(trial.id))
        self._trials[trial.id] = trial

    def get(self, trial_id: uuid.UUID) -> Trial | None:
        return self._trials.get(trial_id)

    def list(self, strategy_id: str | None = None) -> Sequence[Trial]:
        return [t for t in self._trials.values() if strategy_id in (None, t.strategy_id)]

    def count(self) -> int:
        return len(self._trials)
