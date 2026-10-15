from datetime import datetime
from typing import Protocol

from contracts.dataview import DataView
from contracts.intent import Intent


class Strategy(Protocol):
    """A trading strategy. Emits an `Intent` (target weights) and nothing else.

    `asof` must equal `data.asof`; the engine enforces this.
    """

    @property
    def strategy_id(self) -> str: ...

    def generate(self, data: DataView, asof: datetime) -> Intent: ...
