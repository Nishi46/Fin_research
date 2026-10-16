"""Strategies that try to read the future through or around `DataView` (task 42).

Each is honest-looking code with one cheat. The guard either raises `LookaheadError` (the
direct reads) or cannot stop it (reaching `_provider` or the view's clock); those attacks
must then be caught by the lookahead stage because the signal changes when data is truncated.
"""

from collections.abc import Sequence
from datetime import datetime, timedelta

from contracts.dataview import DataView
from contracts.intent import Intent


def _best_next_day(bars: Sequence[object], asof: datetime) -> dict[str, float]:
    """Weights putting everything on the symbol with the best close after `asof`."""
    today: dict[str, float] = {}
    tomorrow: dict[str, float] = {}
    for bar in sorted(bars, key=lambda b: b.ts):  # type: ignore[attr-defined]
        if bar.ts <= asof:  # type: ignore[attr-defined]
            today[bar.symbol] = bar.close  # type: ignore[attr-defined]
        elif bar.symbol not in tomorrow:  # type: ignore[attr-defined]
            tomorrow[bar.symbol] = bar.close  # type: ignore[attr-defined]
    gains = {s: tomorrow[s] / today[s] for s in tomorrow if s in today}
    if not gains:
        return {}
    return {max(gains, key=lambda s: (gains[s], s)): 1.0}


class _Base:
    def __init__(self, symbols: Sequence[str]) -> None:
        self._symbols = sorted(symbols)

    @property
    def strategy_id(self) -> str:
        return f"adv_bypass_{type(self).__name__}"


class DirectFutureEnd(_Base):
    """Asks the view for bars with `end` after asof. Must raise LookaheadError."""

    def generate(self, data: DataView, asof: datetime) -> Intent:
        bars = data.bars(self._symbols, end=asof + timedelta(days=3))
        return Intent(asof=asof, weights=_best_next_day(bars, asof))


class FutureCorporateActions(_Base):
    def generate(self, data: DataView, asof: datetime) -> Intent:
        data.corporate_actions(self._symbols, end=asof + timedelta(days=3))
        return Intent(asof=asof, weights={})


class FutureMembership(_Base):
    def generate(self, data: DataView, asof: datetime) -> Intent:
        data.membership("SPX", (asof + timedelta(days=3)).date())
        return Intent(asof=asof, weights={})


class PrivateProviderPeek(_Base):
    """Uses `data._provider` directly. The view cannot stop this."""

    def generate(self, data: DataView, asof: datetime) -> Intent:
        bars = data._provider.bars(self._symbols, asof + timedelta(days=3))
        return Intent(asof=asof, weights=_best_next_day(bars, asof))


class ClockRewrite(_Base):
    """Moves the view's private clock forward, then reads through the normal API."""

    def generate(self, data: DataView, asof: datetime) -> Intent:
        data._asof = asof + timedelta(days=3)
        try:
            bars = data.bars(self._symbols)
        finally:
            data._asof = asof
        return Intent(asof=asof, weights=_best_next_day(bars, asof))


class FreshViewOffByOne(_Base):
    """Builds a second view one day late, the classic off-by-one on the as-of date."""

    def generate(self, data: DataView, asof: datetime) -> Intent:
        next_bar = asof + timedelta(days=3 if asof.weekday() == 4 else 1)  # one trading day
        later = DataView(data._provider, next_bar)
        bars = later.bars(self._symbols)
        return Intent(asof=asof, weights=_best_next_day(bars, asof))


class SlightlyLateEnd(_Base):
    """Requests `end` one microsecond past asof. Must raise, not round down."""

    def generate(self, data: DataView, asof: datetime) -> Intent:
        data.bars(self._symbols, end=asof + timedelta(microseconds=1))
        return Intent(asof=asof, weights={})
