import math
from collections.abc import Sequence
from datetime import datetime

from contracts.dataview import DataView
from contracts.intent import Intent


class ParamPeakStrategy:
    """Equal-weights everything while the `period`-day square wave is in its up phase.

    The world's drift flips sign with one exact period, so only that `period` earns money;
    one day either side drifts out of phase and earns nothing. A textbook single-point overfit.
    """

    def __init__(self, symbols: Sequence[str], period: int) -> None:
        if period < 2:
            raise ValueError("period must be at least 2")
        self._symbols = sorted(symbols)
        self._period = period

    @property
    def strategy_id(self) -> str:
        return "adv_param_peak"

    @property
    def params(self) -> dict[str, int]:
        return {"period": self._period}

    def generate(self, data: DataView, asof: datetime) -> Intent:
        day = len(data.bars([self._symbols[0]])) - 1
        if math.cos(2 * math.pi * (day + 1) / self._period) > 0:
            weight = 1.0 / len(self._symbols)
            return Intent(asof=asof, weights={s: weight for s in self._symbols})
        return Intent(asof=asof, weights={})
