"""Types shared between the harness components.

The frozen interfaces in `contracts` cover what leaves the harness (strategies, data, registry).
This module covers what the harness components hand to each other. It is written once by the
lead and frozen for the phase: teammates read it, they do not edit it.
"""

import itertools
import math
from collections.abc import Mapping
from datetime import datetime
from typing import Literal, Protocol, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from contracts import CapitalConfig, Intent, MarketState, Order, Stage
from contracts._frozen import FrozenFloatMap
from contracts._time import UtcDatetime


class Regime(BaseModel):
    """A named market period. Half-open: `start` is inside, `end` is not."""

    model_config = ConfigDict(frozen=True)

    name: str = Field(min_length=1)
    start: UtcDatetime
    end: UtcDatetime

    @model_validator(mode="after")
    def _ordered(self) -> Self:
        if self.end <= self.start:
            raise ValueError("regime must end after it starts")
        return self

    def contains(self, ts: datetime) -> bool:
        return self.start <= ts < self.end


class TradeRecord(BaseModel):
    """One round trip. `pnl` is net of all costs; `cost` is the total paid in USD."""

    model_config = ConfigDict(frozen=True)

    symbol: str = Field(min_length=1)
    entry_ts: UtcDatetime
    exit_ts: UtcDatetime
    entry_price: float = Field(gt=0)
    exit_price: float = Field(gt=0)
    shares: float = Field(gt=0)
    cost: float = Field(ge=0)
    pnl: float
    # Held across a known event (for example earnings): the overnight gap is not a normal gap.
    crossed_event: bool = False

    @field_validator("pnl", "cost")
    @classmethod
    def _finite(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("must be finite")
        return value

    @model_validator(mode="after")
    def _exit_after_entry(self) -> Self:
        if self.exit_ts < self.entry_ts:
            raise ValueError("exit cannot precede entry")
        return self


class BacktestResult(BaseModel):
    """Everything a validation stage may use from one backtest.

    `survivorship_biased` has no default on purpose: a result must say whether its universe was.
    `cost_multiplier` is 1.0 for the base run and above 1.0 for cost-sensitivity runs.
    """

    model_config = ConfigDict(frozen=True)

    capital: CapitalConfig
    snapshot_id: str = Field(min_length=1)
    cost_multiplier: float = Field(ge=1.0)
    seed: int
    code_version: str = Field(min_length=1)
    survivorship_biased: bool
    timestamps: tuple[UtcDatetime, ...]
    daily_returns: tuple[float, ...]
    trades: tuple[TradeRecord, ...]

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if len(self.timestamps) != len(self.daily_returns):
            raise ValueError("daily_returns must align one-to-one with timestamps")
        if any(b <= a for a, b in itertools.pairwise(self.timestamps)):
            raise ValueError("timestamps must be strictly increasing")
        if not all(math.isfinite(r) for r in self.daily_returns):
            raise ValueError("daily_returns must be finite")
        return self


class StageResult(BaseModel):
    """Outcome of one validation stage. A failure must say why."""

    model_config = ConfigDict(frozen=True)

    stage: Stage
    passed: bool
    reason: str
    metrics: FrozenFloatMap

    @model_validator(mode="after")
    def _failure_has_reason(self) -> Self:
        if not self.passed and not self.reason.strip():
            raise ValueError("a failed stage must give a reason")
        return self


class Fill(BaseModel):
    model_config = ConfigDict(frozen=True)

    symbol: str = Field(min_length=1)
    side: Literal["buy", "sell"]
    shares: float = Field(gt=0)
    price: float = Field(gt=0)
    ts: UtcDatetime


class Sizer(Protocol):
    """Turns target weights into share counts at the given capital.

    The harness ships a simple default for backtests only. The real sizing layer lives in the
    risk layer (build step 3) and is injected here later, so backtest and live share one path.
    """

    def target_shares(
        self, intent: Intent, capital: CapitalConfig, market: Mapping[str, MarketState]
    ) -> Mapping[str, float]: ...


class Executor(Protocol):
    """Turns an order into a fill. Backtests simulate it; the live engine will wrap a broker."""

    def execute(self, order: Order, market: MarketState) -> Fill: ...
