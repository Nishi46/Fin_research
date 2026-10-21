from collections.abc import Mapping, Sequence
from datetime import datetime

from contracts import FrozenMap
from contracts.dataview import DataView
from contracts.intent import Intent
from contracts.strategy import Params, ParamValue

_PARAM_NAMES = frozenset({"top_n", "lookback"})


def _whole_number(name: str, value: object) -> int:
    # Neighbours are derived by rounding, so whole-number floats are fine; bool is not a knob.
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(f"{name} must be a number, got {value!r}")
    if isinstance(value, float) and not value.is_integer():
        raise ValueError(f"{name} must be a whole number, got {value!r}")
    return int(value)


class BaselineReversal:
    """Equal-weight the `top_n` biggest recent losers. A simple benchmark, not an edge."""

    def __init__(self, symbols: Sequence[str], top_n: int, lookback: int) -> None:
        if top_n < 1 or lookback < 1:
            raise ValueError("top_n and lookback must be at least 1")
        self._symbols = sorted(symbols)
        self._top_n = top_n
        self._lookback = lookback

    @property
    def strategy_id(self) -> str:
        return "baseline_reversal"

    @property
    def params(self) -> Params:
        return FrozenMap[str, ParamValue]({"top_n": self._top_n, "lookback": self._lookback})

    @property
    def lookback_bars(self) -> int:
        return self._lookback + 1

    def with_params(self, params: Mapping[str, ParamValue]) -> "BaselineReversal":
        if set(params) != _PARAM_NAMES:
            raise ValueError(f"params must be exactly {sorted(_PARAM_NAMES)}, got {sorted(params)}")
        return BaselineReversal(
            self._symbols,
            top_n=_whole_number("top_n", params["top_n"]),
            lookback=_whole_number("lookback", params["lookback"]),
        )

    def generate(self, data: DataView, asof: datetime) -> Intent:
        closes: dict[str, list[float]] = {s: [] for s in self._symbols}
        for bar in sorted(data.bars(self._symbols), key=lambda b: b.ts):
            closes[bar.symbol].append(bar.close)
        returns = {
            symbol: series[-1] / series[-1 - self._lookback] - 1.0
            for symbol, series in closes.items()
            if len(series) > self._lookback
        }
        losers = sorted(returns, key=lambda s: (returns[s], s))[: self._top_n]
        if not losers:
            return Intent(asof=asof, weights={})
        return Intent(asof=asof, weights={s: 1.0 / len(losers) for s in losers})
