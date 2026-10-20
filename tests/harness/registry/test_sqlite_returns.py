"""Storage of per-trial return series: precision, size, tamper resistance, old database files."""

import sqlite3
import uuid
import zlib
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import pytest

from contracts.registry import ReturnSeries
from harness.registry.sqlite import SqliteRegistry
from harness.validation.pbo import probability_of_backtest_overfitting

START = datetime(2016, 1, 4, 21, 0, tzinfo=UTC)


def _stamps(n: int) -> tuple[datetime, ...]:
    return tuple(START + timedelta(days=i) for i in range(n))


def _series(group: str, returns: np.ndarray) -> ReturnSeries:
    return ReturnSeries(
        trial_id=uuid.uuid4(),
        group_key=group,
        timestamps=_stamps(len(returns)),
        returns=tuple(float(r) for r in returns),
    )


def test_pbo_from_stored_series_matches_the_float64_original() -> None:
    rng = np.random.default_rng(3)
    original = rng.normal(0.0004, 0.01, size=(1600, 12))
    original[:, 0] += 0.0015
    registry = SqliteRegistry()
    for column in original.T:
        registry.append_returns(_series("g", column))
    stored = registry.returns_for_group("g", limit=100)[::-1]  # back to insertion order
    restored = np.column_stack([np.asarray(s.returns, dtype=np.float64) for s in stored])
    assert restored.shape == original.shape
    assert np.allclose(restored, original, rtol=1e-6, atol=1e-9)
    assert probability_of_backtest_overfitting(restored) == pytest.approx(
        probability_of_backtest_overfitting(original), abs=0.01
    )


def test_timestamps_survive_with_their_time_of_day() -> None:
    registry = SqliteRegistry()
    series = _series("g", np.linspace(-0.01, 0.01, 50))
    registry.append_returns(series)
    (stored,) = registry.returns_for_group("g", limit=1)
    assert stored.timestamps == series.timestamps
    assert all(t.tzinfo is not None and t.utcoffset() == timedelta(0) for t in stored.timestamps)


@pytest.mark.parametrize(("days", "max_bytes"), [(1000, 8_000), (2500, 20_000)])
def test_payload_stays_small(days: int, max_bytes: int) -> None:
    registry = SqliteRegistry()
    registry.append_returns(_series("g", np.random.default_rng(0).normal(0, 0.01, days)))
    (size,) = registry.connection.execute("SELECT length(payload) FROM trial_returns").fetchone()
    assert size < max_bytes


def test_raw_rewrites_of_a_stored_series_are_blocked() -> None:
    registry = SqliteRegistry()
    series = _series("g", np.ones(10) * 0.001)
    registry.append_returns(series)
    conn = registry.connection
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        conn.execute("UPDATE trial_returns SET group_key = 'x'")
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        conn.execute("DELETE FROM trial_returns")
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        conn.execute(
            "INSERT OR REPLACE INTO trial_returns (trial_id, group_key, n, payload, created_at)"
            " VALUES (?, 'x', 1, ?, 'now')",
            (str(series.trial_id), zlib.compress(b"")),
        )
    assert len(registry.returns_for_group("g", limit=5)) == 1


def test_a_database_created_before_trial_returns_gains_the_table(tmp_path: Path) -> None:
    path = str(tmp_path / "registry.db")
    first = SqliteRegistry(path)
    first.connection.execute("DROP TABLE trial_returns")  # what an older file looks like
    first.connection.close()
    reopened = SqliteRegistry(path)
    reopened.append_returns(_series("g", np.ones(10) * 0.001))
    assert len(reopened.returns_for_group("g", limit=5)) == 1


def test_series_persist_across_connections(tmp_path: Path) -> None:
    path = str(tmp_path / "registry.db")
    first = SqliteRegistry(path)
    series = _series("g", np.linspace(-0.01, 0.01, 20))
    first.append_returns(series)
    first.connection.close()
    (stored,) = SqliteRegistry(path).returns_for_group("g", limit=5)
    assert stored.trial_id == series.trial_id
