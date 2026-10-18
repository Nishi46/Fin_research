from collections.abc import Sequence
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest

from contracts.data import Bar, CorporateAction, CorporateActionKind
from harness.data.cache import ActionsStore, ParquetCache
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


def _action(symbol: str, kind: CorporateActionKind, day: int, value: float) -> CorporateAction:
    return CorporateAction(
        symbol=symbol,
        kind=kind,
        effective_at=datetime(2024, 3, day, tzinfo=UTC),
        value=value,
    )


def _sample_actions() -> list[CorporateAction]:
    return [
        _action("AAA", CorporateActionKind.SPLIT, 5, 2.0),
        _action("AAA", CorporateActionKind.DIVIDEND, 1, 0.25),
        _action("BBB", CorporateActionKind.DIVIDEND, 1, 0.5),
    ]


def test_actions_round_trip(tmp_path: Path) -> None:
    store = ActionsStore(tmp_path)
    actions = _sample_actions()
    store.store(actions)
    loaded = store.load()
    assert sorted(loaded, key=repr) == sorted(actions, key=repr)
    assert all(a.effective_at.tzinfo is not None for a in loaded)
    assert all(a.effective_at.utcoffset() == timedelta(0) for a in loaded)


def test_actions_missing_file_loads_empty(tmp_path: Path) -> None:
    assert ActionsStore(tmp_path / "nope").load() == []


def test_actions_ordering_is_deterministic(tmp_path: Path) -> None:
    actions = _sample_actions()
    a, b = ActionsStore(tmp_path / "a"), ActionsStore(tmp_path / "b")
    a.store(actions)
    b.store(list(reversed(actions)))
    assert a.load() == b.load()
    keys = [(x.effective_at, x.symbol, x.kind.value) for x in a.load()]
    assert keys == sorted(keys)


def test_actions_store_overwrites(tmp_path: Path) -> None:
    store = ActionsStore(tmp_path)
    store.store(_sample_actions())
    store.store(_sample_actions()[:1])
    assert store.load() == _sample_actions()[:1]


def test_actions_store_empty_writes_valid_empty_file(tmp_path: Path) -> None:
    store = ActionsStore(tmp_path)
    store.store(_sample_actions())
    store.store([])
    assert (tmp_path / "actions.parquet").exists()
    assert store.load() == []


def test_actions_non_utc_input_normalised(tmp_path: Path) -> None:
    est = timezone(timedelta(hours=-5))
    action = CorporateAction(
        symbol="AAA",
        kind=CorporateActionKind.SPLIT,
        effective_at=datetime(2024, 3, 5, 9, 30, tzinfo=est),
        value=2.0,
    )
    store = ActionsStore(tmp_path)
    store.store([action])
    (loaded,) = store.load()
    assert loaded.effective_at == datetime(2024, 3, 5, 14, 30, tzinfo=UTC)
    assert loaded.effective_at.utcoffset() == timedelta(0)
