import uuid
from collections.abc import Sequence

from contracts.registry import (
    DuplicateTrialError,
    ReturnSeries,
    ReturnSeriesMismatchError,
    Trial,
)


class InMemoryRegistry:
    def __init__(self) -> None:
        self._trials: dict[uuid.UUID, Trial] = {}
        self._series: dict[uuid.UUID, ReturnSeries] = {}
        self._groups: dict[str, list[ReturnSeries]] = {}

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

    def append_returns(self, series: ReturnSeries) -> None:
        if series.trial_id in self._series:
            raise DuplicateTrialError(str(series.trial_id))
        group = self._groups.setdefault(series.group_key, [])
        if group and group[0].timestamps != series.timestamps:
            raise ReturnSeriesMismatchError(series.group_key)
        self._series[series.trial_id] = series
        group.append(series)

    def returns_for_group(
        self, group_key: str, *, limit: int, exclude: uuid.UUID | None = None
    ) -> Sequence[ReturnSeries]:
        if limit < 1:
            raise ValueError("limit must be at least 1")
        group = [s for s in self._groups.get(group_key, []) if s.trial_id != exclude]
        return group[::-1][:limit]
