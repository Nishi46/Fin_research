from collections.abc import Iterator, Sequence
from datetime import datetime
from typing import Protocol, Self

from pydantic import BaseModel, ConfigDict, model_validator

from contracts._time import UtcDatetime


class Split(BaseModel):
    """One train/test split. The gap between `train_end` and `test_start` is the purge."""

    model_config = ConfigDict(frozen=True)

    train_start: UtcDatetime
    train_end: UtcDatetime
    test_start: UtcDatetime
    test_end: UtcDatetime

    @model_validator(mode="after")
    def _ordered(self) -> Self:
        if self.train_start > self.train_end:
            raise ValueError("train window is inverted")
        if self.test_start > self.test_end:
            raise ValueError("test window is inverted")
        if self.train_end >= self.test_start:
            raise ValueError("train must end strictly before test starts")
        return self


class Splitter(Protocol):
    """Purged walk-forward splits. `purge` and `embargo` are counted in bars.

    Random k-fold is not allowed: it leaks the future into training.
    """

    def splits(
        self, timestamps: Sequence[datetime], *, n_splits: int, purge: int, embargo: int
    ) -> Iterator[Split]: ...
