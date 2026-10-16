"""Parquet cache, one file per symbol, with incremental fetch."""

import re
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd  # type: ignore[import-untyped]

from contracts.data import Bar

Fetcher = Callable[[str, datetime | None, datetime], list[Bar]]
_SYMBOL = re.compile(r"^[A-Z0-9][A-Z0-9.\-]{0,9}$")
_COLUMNS = ["symbol", "ts", "open", "high", "low", "close", "volume"]


class ParquetCache:
    def __init__(self, root: Path) -> None:
        self._root = root

    def _path(self, symbol: str) -> Path:
        if not _SYMBOL.match(symbol):
            raise ValueError(f"invalid symbol for cache: {symbol!r}")
        return self._root / "bars" / f"{symbol}.parquet"

    def load(self, symbol: str) -> list[Bar]:
        path = self._path(symbol)
        if not path.exists():
            return []
        frame = pd.read_parquet(path)
        return [
            Bar(
                symbol=str(r.symbol),
                ts=pd.Timestamp(r.ts).to_pydatetime(),
                open=float(r.open),
                high=float(r.high),
                low=float(r.low),
                close=float(r.close),
                volume=float(r.volume),
            )
            for r in frame.itertuples(index=False)
        ]

    def store(self, symbol: str, bars: list[Bar]) -> None:
        path = self._path(symbol)
        path.parent.mkdir(parents=True, exist_ok=True)
        frame = pd.DataFrame(
            [{c: getattr(b, c) for c in _COLUMNS} for b in sorted(bars, key=lambda b: b.ts)],
            columns=_COLUMNS,
        )
        frame["ts"] = pd.to_datetime(frame["ts"], utc=True)
        frame.to_parquet(path, index=False)

    def fetch_incremental(self, symbol: str, end: datetime, fetcher: Fetcher) -> list[Bar]:
        """Return cached bars up to `end`, fetching only what the cache lacks.

        Known limitation: bars already cached are never re-fetched, so later vendor
        revisions of old bars are not picked up until the cache file is removed.
        """
        cached = self.load(symbol)
        last = cached[-1].ts if cached else None
        if last is None or last < end:
            start = None if last is None else last + timedelta(microseconds=1)
            fresh = fetcher(symbol, start, end)
            merged = {b.ts: b for b in cached}
            merged.update({b.ts: b for b in fresh})
            cached = [merged[ts] for ts in sorted(merged)]
            if fresh:
                self.store(symbol, cached)
        return [b for b in cached if b.ts <= end]
