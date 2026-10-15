import uuid
from collections.abc import Sequence
from decimal import Decimal
from enum import StrEnum
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from contracts._frozen import FrozenFloatMap, FrozenJsonMap
from contracts._time import UtcDatetime


class DuplicateTrialError(Exception):
    """Raised when a trial id is appended twice. The original is left untouched."""


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


class Registry(Protocol):
    """Append-only trial log. There is deliberately no way to change or remove a trial.

    `count()` is the trial count that feeds the Deflated Sharpe Ratio.
    """

    def append(self, trial: Trial) -> None: ...

    def get(self, trial_id: uuid.UUID) -> Trial | None: ...

    def list(self, strategy_id: str | None = None) -> Sequence[Trial]: ...

    def count(self) -> int: ...
