import os
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from contracts.dataview import DataView, LookaheadError
from contracts.intent import Intent
from contracts.runtime import RunStatus
from contracts.strategy import Strategy
from engine.config import EngineConfig
from engine.run import AsofMismatchError, RunResult, run_once
from fakes.data import InMemoryDataProvider, synthetic_bars
from fakes.runtime import InMemoryRunLog
from strategies.baseline import BaselineReversal
from tests.helpers import T0

SYMBOLS = ["AAA", "BBB", "CCC", "DDD"]
ASOF = T0 + timedelta(days=20)


def make_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> EngineConfig:
    monkeypatch.chdir(tmp_path)
    return EngineConfig.from_env({"LAB_REGISTRY_URL": "memory://"}, now=T0)


def go(config: EngineConfig, strategy: Strategy, runlog: InMemoryRunLog) -> RunResult:
    provider = InMemoryDataProvider(synthetic_bars(SYMBOLS, start=T0, days=30, seed=7))
    return run_once(config=config, provider=provider, strategy=strategy, runlog=runlog, asof=ASOF)


def test_run_once_end_to_end_from_clean_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = make_config(tmp_path, monkeypatch)
    runlog = InMemoryRunLog()

    result = go(config, BaselineReversal(symbols=SYMBOLS, top_n=2, lookback=5), runlog)

    assert set(result.intent.weights) <= set(SYMBOLS)
    assert sum(result.intent.weights.values()) <= 1.0
    assert result.capital.amount == config.capital.amount
    assert [e.status for e in runlog.events(result.run_id)] == [
        RunStatus.STARTED,
        RunStatus.SUCCEEDED,
    ]
    assert list(tmp_path.iterdir()) == []  # no files written: all state is behind interfaces


def test_run_once_is_deterministic(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = make_config(tmp_path, monkeypatch)
    strategy = BaselineReversal(symbols=SYMBOLS, top_n=2, lookback=5)
    first = go(config, strategy, InMemoryRunLog())
    second = go(config, strategy, InMemoryRunLog())
    assert first.intent == second.intent


class PeekingStrategy:
    strategy_id = "peeker"

    def generate(self, data: DataView, asof: datetime) -> Intent:
        data.bars(["AAA"], end=asof + timedelta(days=1))
        raise AssertionError("unreachable: the view must refuse the read")


class WrongAsofStrategy:
    strategy_id = "wrong_asof"

    def generate(self, data: DataView, asof: datetime) -> Intent:
        return Intent(asof=asof + timedelta(days=1), weights={})


def test_lookahead_is_logged_as_failure_and_reraised(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = make_config(tmp_path, monkeypatch)
    runlog = InMemoryRunLog()
    with pytest.raises(LookaheadError):
        go(config, PeekingStrategy(), runlog)
    latest = runlog.latest()
    assert latest is not None
    assert latest.status is RunStatus.FAILED
    assert latest.detail is not None
    assert "LookaheadError" in latest.detail


def test_intent_with_wrong_asof_fails_the_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = make_config(tmp_path, monkeypatch)
    runlog = InMemoryRunLog()
    with pytest.raises(ValueError, match="asof"):
        go(config, WrongAsofStrategy(), runlog)
    latest = runlog.latest()
    assert latest is not None
    assert latest.status is RunStatus.FAILED


def test_config_ignores_the_process_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LAB_CAPITAL_USD", "999999")
    assert not any(k.startswith("LAB_") and k != "LAB_CAPITAL_USD" for k in os.environ)
    config = make_config(tmp_path, monkeypatch)
    assert config.capital.source == "default"


def test_mismatched_asof_raises_the_typed_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = make_config(tmp_path, monkeypatch)
    with pytest.raises(AsofMismatchError):
        go(config, WrongAsofStrategy(), InMemoryRunLog())
