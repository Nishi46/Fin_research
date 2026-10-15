from collections.abc import Sequence
from datetime import date, datetime
from enum import StrEnum
from typing import Protocol, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from contracts._time import UtcDatetime


class Bar(BaseModel):
    """One daily bar. `ts` is the bar's close time: the bar does not exist before it."""

    model_config = ConfigDict(frozen=True)

    symbol: str
    ts: UtcDatetime
    open: float = Field(gt=0)
    high: float = Field(gt=0)
    low: float = Field(gt=0)
    close: float = Field(gt=0)
    volume: float = Field(ge=0)

    @model_validator(mode="after")
    def _consistent_ohlc(self) -> Self:
        if not self.low <= min(self.open, self.close) <= max(self.open, self.close) <= self.high:
            raise ValueError("inconsistent OHLC: need low <= open, close <= high")
        return self


class CorporateActionKind(StrEnum):
    SPLIT = "split"
    DIVIDEND = "dividend"


class CorporateAction(BaseModel):
    """`value` is the split ratio (2.0 for 2-for-1) or the dividend per share."""

    model_config = ConfigDict(frozen=True)

    symbol: str
    kind: CorporateActionKind
    effective_at: UtcDatetime
    value: float = Field(gt=0)


class Snapshot(BaseModel):
    """Pinned data version. `id` is a content hash so results are reproducible."""

    model_config = ConfigDict(frozen=True)

    id: str
    survivorship_biased: bool
    start: UtcDatetime
    end: UtcDatetime


class DataProvider(Protocol):
    """Source of market data. Implementations must never return data after `end`/`on`;
    `DataView` re-checks anyway."""

    def snapshot(self) -> Snapshot: ...

    def bars(self, symbols: Sequence[str], end: datetime) -> Sequence[Bar]: ...

    def corporate_actions(
        self, symbols: Sequence[str], end: datetime
    ) -> Sequence[CorporateAction]: ...

    def membership(self, index: str, on: date) -> frozenset[str]: ...
