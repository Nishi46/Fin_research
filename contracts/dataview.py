from collections.abc import Sequence
from datetime import date, datetime

from contracts._time import ensure_utc
from contracts.data import Bar, CorporateAction, DataProvider


class LookaheadError(Exception):
    """Raised when anything tries to read data from after the view's `asof`."""


class DataView:
    """A provider restricted to data that existed at `asof`.

    This is the one concrete class in `contracts/`: the guard is the contract, so Phase 1
    must not reimplement it differently. It checks both the requested range and what the
    provider actually returns, so a buggy provider cannot leak the future.

    Python cannot hide `_provider` from code that holds the view, so this guard stops honest
    mistakes and buggy providers, not a strategy determined to cheat. The real boundary for
    untrusted strategy code is the sandbox (tools/check_strategy_sandbox.py as a tripwire, and
    process-level isolation in Phase 4).
    """

    def __init__(self, provider: DataProvider, asof: datetime) -> None:
        self._provider = provider
        self._asof = ensure_utc(asof)

    @property
    def asof(self) -> datetime:
        return self._asof

    def _limit(self, end: datetime | None) -> datetime:
        if end is None:
            return self._asof
        end = ensure_utc(end)
        if end > self._asof:
            raise LookaheadError(f"requested end {end} is after asof {self._asof}")
        return end

    def bars(self, symbols: Sequence[str], end: datetime | None = None) -> list[Bar]:
        limit = self._limit(end)
        bars = list(self._provider.bars(symbols, limit))
        if any(bar.ts > self._asof for bar in bars):
            raise LookaheadError("provider returned bars after asof")
        return bars

    def corporate_actions(
        self, symbols: Sequence[str], end: datetime | None = None
    ) -> list[CorporateAction]:
        limit = self._limit(end)
        actions = list(self._provider.corporate_actions(symbols, limit))
        if any(action.effective_at > self._asof for action in actions):
            raise LookaheadError("provider returned corporate actions after asof")
        return actions

    def membership(self, index: str, on: date) -> frozenset[str]:
        if on > self._asof.date():
            raise LookaheadError(f"membership date {on} is after asof {self._asof}")
        return self._provider.membership(index, on)
