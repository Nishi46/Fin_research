import uuid
from collections.abc import Sequence
from decimal import Decimal
from enum import StrEnum
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator

from contracts._frozen import FrozenFloatMap, FrozenJsonMap
from contracts._time import UtcDatetime


class DuplicateTrialError(Exception):
    """Raised when a trial id is appended twice. The original is left untouched."""


class ReturnSeriesMismatchError(Exception):
    """Raised when a series' timestamps differ from the first series stored for its group."""


class Stage(StrEnum):
    """Validation pipeline stages, in the fixed order they run."""

    LOOKAHEAD = "lookahead"
    WALK_FORWARD = "walk_forward"
    TRIAL_ADJUSTED = "trial_adjusted"
    COST_SENSITIVITY = "cost_sensitivity"
    STABILITY = "stability"
    FLOORS = "floors"
    PAPER = "paper"
    OWNER_GATE = "owner_gate"


class Outcome(StrEnum):
    REJECTED = "rejected"
    PASSED = "passed"
    ERROR = "error"


class Trial(BaseModel):
    """One attempt, logged forever. `survivorship_biased` has no default on purpose."""

    model_config = ConfigDict(frozen=True)

    id: uuid.UUID
    created_at: UtcDatetime
    strategy_id: str
    code_hash: str
    params: FrozenJsonMap
    capital_amount: Decimal = Field(gt=0)
    capital_currency: str
    data_snapshot_id: str
    survivorship_biased: bool
    stage_reached: Stage
    outcome: Outcome
    metrics: FrozenFloatMap


class ReturnSeries(BaseModel):
    """A trial's net daily returns, kept next to its row so any PBO can be recomputed.

    `group_key` names the set of trials that are comparable (same data, window, costs, capital).
    """

    model_config = ConfigDict(frozen=True)

    trial_id: uuid.UUID
    group_key: str
    timestamps: tuple[UtcDatetime, ...]
    returns: tuple[float, ...]

    @model_validator(mode="after")
    def _aligned_and_non_empty(self) -> "ReturnSeries":
        if len(self.timestamps) != len(self.returns):
            raise ValueError("timestamps and returns must have the same length")
        if not self.timestamps:
            raise ValueError("a return series needs at least one observation")
        return self


class Registry(Protocol):
    """Append-only trial log. There is deliberately no way to change or remove a trial.

    `count()` is the trial count that feeds the Deflated Sharpe Ratio.
    """

    def append(self, trial: Trial) -> None: ...

    def get(self, trial_id: uuid.UUID) -> Trial | None: ...

    def list(self, strategy_id: str | None = None) -> Sequence[Trial]: ...

    def count(self) -> int: ...

    def append_returns(self, series: ReturnSeries) -> None:
        """Store a trial's return series once. Raises `DuplicateTrialError` for a second series
        for the same trial and `ReturnSeriesMismatchError` if the timestamps differ from the
        first series of the group. A refused series stores nothing."""
        ...

    def returns_for_group(
        self, group_key: str, *, limit: int, exclude: uuid.UUID | None = None
    ) -> Sequence[ReturnSeries]:
        """Up to `limit` series of the group, most recently appended first."""
        ...
