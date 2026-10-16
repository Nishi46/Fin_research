from datetime import datetime, timedelta
from decimal import Decimal

import pytest

from contracts import CapitalConfig
from fakes.data import InMemoryDataProvider
from harness.engine.backtest import BacktestConfig
from harness.engine.runner import detect_code_version, run_backtest
from tests.harness.engine.test_backtest import Scheduled, flat
from tests.helpers import T0

END = T0 + timedelta(days=9)


def capital(amount: str = "10000") -> CapitalConfig:
    return CapitalConfig(amount=Decimal(amount), effective_at=T0, source="website")


def go(amount: str = "10000", multiplier: float = 1.0, seed: int = 7, version: str = "v1"):  # type: ignore[no-untyped-def]
    return run_backtest(
        provider=InMemoryDataProvider(flat("AAA", 10)),
        strategy=Scheduled({0: {"AAA": 0.5}}),
        symbols=("AAA",),
        start=T0,
        end=END,
        capital=capital(amount),
        cost_multiplier=multiplier,
        seed=seed,
        code_version=version,
    )


def test_result_records_capital_seed_version_and_snapshot() -> None:
    result = go()
    assert result.capital.amount == Decimal("10000")
    assert result.seed == 7
    assert result.code_version == "v1"
    assert result.snapshot_id.startswith("sha256:")
    assert result.survivorship_biased is True
    assert result.cost_multiplier == 1.0
    assert len(result.timestamps) == len(result.daily_returns) == 10


def test_capital_is_required_not_defaulted() -> None:
    with pytest.raises(TypeError):
        run_backtest(  # type: ignore[call-arg]
            provider=InMemoryDataProvider(flat("AAA", 10)),
            strategy=Scheduled({}),
            symbols=("AAA",),
            start=T0,
            end=END,
        )


def test_doubling_costs_doubles_the_cost_paid() -> None:
    base, doubled = go(), go(multiplier=2.0)
    assert doubled.cost_multiplier == 2.0
    assert sum(t.cost for t in doubled.trades) == pytest.approx(
        2 * sum(t.cost for t in base.trades)
    )
    assert sum(doubled.daily_returns) < sum(base.daily_returns)


def test_cost_multiplier_below_one_is_refused() -> None:
    with pytest.raises(ValueError, match="at least 1"):
        go(multiplier=0.5)


def test_same_inputs_give_identical_results() -> None:
    assert go() == go()


def test_capital_changes_positions_and_cost_with_identical_signals() -> None:
    small, large = go("500"), go("500000")
    assert small.trades[0].shares < large.trades[0].shares
    assert small.trades[0].cost < large.trades[0].cost


def test_code_version_is_detected_when_not_given() -> None:
    version = detect_code_version()
    assert version
    result = run_backtest(
        provider=InMemoryDataProvider(flat("AAA", 10)),
        strategy=Scheduled({}),
        symbols=("AAA",),
        start=T0,
        end=END,
        capital=capital(),
    )
    assert result.code_version == version


def _flagged(events: dict[str, tuple[datetime, ...]]):  # type: ignore[no-untyped-def]
    return run_backtest(
        provider=InMemoryDataProvider(flat("AAA", 10)),
        strategy=Scheduled({0: {"AAA": 0.5}}),
        symbols=("AAA",),
        start=T0,
        end=END,
        capital=capital(),
        config=BacktestConfig(event_dates=events),
    )


def test_trade_spanning_an_event_is_flagged_but_kept() -> None:
    result = _flagged({"AAA": (T0 + timedelta(days=4),)})
    assert [t.crossed_event for t in result.trades] == [True]


def test_trade_not_spanning_an_event_is_not_flagged() -> None:
    result = _flagged({"AAA": (T0 + timedelta(days=30),)})
    assert [t.crossed_event for t in result.trades] == [False]


def test_no_event_dates_means_no_flags() -> None:
    assert all(not t.crossed_event for t in _flagged({}).trades)
    assert len(_flagged({}).trades) == 1
