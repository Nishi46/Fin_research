from collections.abc import Sequence
from datetime import datetime

from contracts.dataview import DataView
from contracts.intent import Intent


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
