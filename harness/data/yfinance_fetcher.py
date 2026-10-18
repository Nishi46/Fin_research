"""Real fetcher for `YFinanceCrossChecker`. Cross-check use only, never a primary source.

Yahoo's terms allow personal use only and the unofficial API can change or block without notice,
so every call goes through the rate limiter and vendor errors propagate (a failed fetch must not
look like "no data").

Basis: with `auto_adjust=False`, Yahoo's `Close` is split-adjusted but not dividend-adjusted.
Alpaca bars are raw, so the caller must split-adjust the primary side before reconciling (see
`reconcile` docstring: both inputs must share one adjustment basis).
"""

from collections.abc import Callable, Mapping, Sequence
from datetime import date, timedelta
from typing import Any

import pandas as pd  # type: ignore[import-untyped]

from harness.data.crosscheck import YFinanceFetcher
from harness.data.ratelimit import RateLimiter

# (symbol, start, end_exclusive) -> frame indexed by session with Open/High/Low/Close/Volume
History = Callable[[str, date, date], pd.DataFrame]
_PRICE_COLUMNS = ("Open", "High", "Low", "Close", "Volume")


def _yfinance_history(symbol: str, start: date, end: date) -> pd.DataFrame:
    import yfinance  # type: ignore[import-not-found,import-untyped,unused-ignore]  # lazy: optional dependency

    return yfinance.Ticker(symbol).history(
        start=start.isoformat(), end=end.isoformat(), auto_adjust=False, actions=False
    )


def make_yfinance_fetcher(
    limiter: RateLimiter, *, history: History | None = None
) -> YFinanceFetcher:
    fetch_history = history or _yfinance_history

    def fetch(symbol: str, start: date, end: date) -> Sequence[Mapping[str, Any]]:
        limiter.acquire()
        # yfinance treats `end` as exclusive; the cross-checker's range is inclusive.
        frame = fetch_history(symbol, start, end + timedelta(days=1))
        if frame.empty:
            return []
        rows: list[Mapping[str, Any]] = []
        for ts, row in frame.iterrows():
            values = [row[c] for c in _PRICE_COLUMNS]
            if any(pd.isna(v) for v in values):
                continue
            rows.append(
                {"Date": ts.date().isoformat(), **{c: float(row[c]) for c in _PRICE_COLUMNS}}
            )
        return rows

    return fetch
