from collections.abc import Mapping
from datetime import datetime
from typing import Protocol, Self, runtime_checkable

from contracts.dataview import DataView
from contracts.intent import Intent

ParamValue = int | float
Params = Mapping[str, ParamValue]


class Strategy(Protocol):
    """A trading strategy. Emits an `Intent` (target weights) and nothing else.

    `asof` must equal `data.asof`; the engine enforces this.
    """

    @property
    def strategy_id(self) -> str: ...

    def generate(self, data: DataView, asof: datetime) -> Intent: ...


@runtime_checkable
class ParametrizedStrategy(Strategy, Protocol):
    """A `Strategy` the harness can re-build with other parameters.

    The pipeline judges parameter stability by running neighbours of `params`, so it must be able
    to make them. Rules (the conformance suite in tests/contract_suite checks each one):

    * `params` is a fresh read-only mapping of numeric knobs, the same ones `with_params` accepts.
    * `with_params(params)` returns a NEW instance and never mutates `self`;
      `with_params(self.params)` behaves identically to `self`.
    * `lookback_bars` is the number of sessions of history needed before `generate` can return a
      non-empty intent; with less history it returns an empty `Intent`, it does not raise.
    * `generate` is a pure function of `(data, asof, params)`: no state carried between calls.
    """

    @property
    def params(self) -> Params: ...

    @property
    def lookback_bars(self) -> int: ...

    def with_params(self, params: Params) -> Self: ...
