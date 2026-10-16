from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

import pytest

from contracts.data import Bar
from harness.data.cache import ParquetCache
from tests.data_fixtures import normal_series


class CountingFetcher:
    def __init__(self, bars: Sequence[Bar]) -> None:
        self._bars = list(bars)
        self.calls: list[datetime | None] = []

    def __call__(self, symbol: str, start: datetime | None, end: datetime) -> list[Bar]:
        self.calls.append(start)
        return [
            b
            for b in self._bars
            if b.symbol == symbol and (start is None or b.ts >= start) and b.ts <= end
        ]


def test_round_trip(tmp_path: Path) -> None:
    cache = ParquetCache(tmp_path)
    bars = normal_series()
    cache.store("AAA", bars)
    assert cache.load("AAA") == bars


def test_missing_symbol_loads_empty(tmp_path: Path) -> None:
    assert ParquetCache(tmp_path).load("ZZZ") == []


def test_incremental_fetch_only_asks_for_new_data(tmp_path: Path) -> None:
    bars = normal_series(n=20)
    fetcher = CountingFetcher(bars)
    cache = ParquetCache(tmp_path)
    first = cache.fetch_incremental("AAA", bars[9].ts, fetcher)
    assert first == bars[:10]
    second = cache.fetch_incremental("AAA", bars[-1].ts, fetcher)
    assert second == bars
    assert fetcher.calls[0] is None
    assert fetcher.calls[1] is not None
    assert fetcher.calls[1] > bars[9].ts


def test_no_fetch_when_cache_already_covers_end(tmp_path: Path) -> None:
    bars = normal_series(n=10)
    fetcher = CountingFetcher(bars)
    cache = ParquetCache(tmp_path)
    cache.fetch_incremental("AAA", bars[-1].ts, fetcher)
    cache.fetch_incremental("AAA", bars[5].ts, fetcher)
    assert len(fetcher.calls) == 1


def test_result_never_exceeds_end(tmp_path: Path) -> None:
    bars = normal_series(n=10)
    cache = ParquetCache(tmp_path)
    cache.fetch_incremental("AAA", bars[-1].ts, CountingFetcher(bars))
    got = cache.fetch_incremental("AAA", bars[3].ts, CountingFetcher(bars))
    assert got == bars[:4]


def test_symbol_cannot_escape_cache_dir(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="symbol"):
        ParquetCache(tmp_path).load("../evil")
