from collections.abc import Sequence
from datetime import datetime

from contracts.dataview import DataView
from contracts.intent import Intent
from tests.adversarial.strategies._common import closes_by_symbol


class ReversalStrategy:
    """Equal-weight the `top_n` worst `lookback`-day performers. Honest: uses only the view."""

    def __init__(
        self,
        symbols: Sequence[str],
        lookback: int = 10,
        top_n: int = 3,
        strategy_id: str = "adv_reversal",
    ) -> None:
        self._symbols = sorted(symbols)
        self._lookback = lookback
        self._top_n = top_n
        self._id = strategy_id

    @property
    def strategy_id(self) -> str:
        return self._id

    @property
    def params(self) -> dict[str, int]:
        return {"lookback": self._lookback, "top_n": self._top_n}

    def generate(self, data: DataView, asof: datetime) -> Intent:
        closes = closes_by_symbol(data, self._symbols)
        score = {
            s: c[-1] / c[-1 - self._lookback] - 1.0
            for s, c in closes.items()
            if len(c) > self._lookback
        }
        losers = sorted(score, key=lambda s: (score[s], s))[: self._top_n]
        if not losers:
            return Intent(asof=asof, weights={})
        return Intent(asof=asof, weights={s: 1.0 / len(losers) for s in losers})
