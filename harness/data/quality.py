"""Gap and stale-bar detection."""

import itertools
from collections import defaultdict
from collections.abc import Sequence

from contracts.data import Bar
from harness.data.calendar import trading_sessions
from harness.data.common import Issue

MIN_STALE_RUN = 2


def _by_symbol(bars: Sequence[Bar]) -> dict[str, list[Bar]]:
    grouped: dict[str, list[Bar]] = defaultdict(list)
    for bar in sorted(bars, key=lambda b: b.ts):
        grouped[bar.symbol].append(bar)
    return grouped


def find_gaps(bars: Sequence[Bar]) -> list[Issue]:
    """NYSE sessions between a symbol's first and last bar that have no bar."""
    issues: list[Issue] = []
    for symbol, series in _by_symbol(bars).items():
        present = {b.ts.date() for b in series}
        for session in trading_sessions(series[0].ts.date(), series[-1].ts.date()):
            if session not in present:
                issues.append(Issue("gap", symbol, None, f"no bar for session {session}"))
    return issues


def find_stale_bars(bars: Sequence[Bar], *, min_run: int = MIN_STALE_RUN) -> list[Issue]:
    """Bars that repeat the previous bar's OHLC, or trade zero volume, in a run of `min_run`+.

    Each bar inside a qualifying run is reported.
    """
    issues: list[Issue] = []
    for symbol, series in _by_symbol(bars).items():
        run: list[Bar] = []
        for prev, cur in itertools.pairwise(series):
            same = (prev.open, prev.high, prev.low, prev.close) == (
                cur.open,
                cur.high,
                cur.low,
                cur.close,
            )
            if same or cur.volume == 0:
                run.append(cur)
                continue
            issues.extend(_flush(symbol, run, min_run))
            run = []
        issues.extend(_flush(symbol, run, min_run))
    return issues


def _flush(symbol: str, run: list[Bar], min_run: int) -> list[Issue]:
    if len(run) < min_run:
        return []
    return [Issue("stale", symbol, b.ts, "repeats prior bar or has zero volume") for b in run]
