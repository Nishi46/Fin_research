import random
from collections.abc import Sequence
from datetime import datetime

from contracts.dataview import DataView
from contracts.intent import Intent

HOLD_DAYS = 5


class NoiseStrategy:
    """Holds one randomly chosen symbol for `HOLD_DAYS` days. The seed is the only parameter."""

    def __init__(self, symbols: Sequence[str], seed: int) -> None:
        self._symbols = sorted(symbols)
        self._seed = seed

    @staticmethod
    def choice(symbols: Sequence[str], seed: int, day_index: int) -> str:
        rng = random.Random(f"noise:{seed}:{day_index // HOLD_DAYS}")
        return rng.choice(sorted(symbols))

    @property
    def strategy_id(self) -> str:
        return f"adv_noise_{self._seed}"

    @property
    def params(self) -> dict[str, int]:
        return {"seed": self._seed}

    def generate(self, data: DataView, asof: datetime) -> Intent:
        first = [b for b in data.bars([self._symbols[0]])]
        if not first:
            return Intent(asof=asof, weights={})
        pick = self.choice(self._symbols, self._seed, len(first) - 1)
        return Intent(asof=asof, weights={pick: 1.0})
