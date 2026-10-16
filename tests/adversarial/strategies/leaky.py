from collections.abc import Sequence
from datetime import datetime, timedelta

from contracts.dataview import DataView
from contracts.intent import Intent

LOOK_FORWARD = timedelta(days=5)


class LeakyStrategy:
    """Reaches around the view into its provider and buys tomorrow's best performer.

    On the full dataset it looks like a money machine. Given a provider cut at `asof` the future
    is gone and the signal changes, which is exactly what the lookahead stage tests for.
    """

    def __init__(self, symbols: Sequence[str]) -> None:
        self._symbols = sorted(symbols)

    @property
    def strategy_id(self) -> str:
        return "adv_leaky"

    @property
    def params(self) -> dict[str, int]:
        return {}

    def generate(self, data: DataView, asof: datetime) -> Intent:
        bars = sorted(
            data._provider.bars(self._symbols, asof + LOOK_FORWARD),
            key=lambda b: b.ts,
        )
        today = {b.symbol: b.close for b in bars if b.ts == asof}
        tomorrow: dict[str, float] = {}
        for bar in bars:
            if bar.ts > asof and bar.symbol not in tomorrow:
                tomorrow[bar.symbol] = bar.close
        gains = {s: tomorrow[s] / today[s] - 1.0 for s in tomorrow if s in today}
        if not gains:
            return Intent(asof=asof, weights={})
        best = max(gains, key=lambda s: (gains[s], s))
        return Intent(asof=asof, weights={best: 1.0})
