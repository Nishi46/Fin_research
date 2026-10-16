from datetime import UTC, datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError

from contracts import CapitalConfig, Stage
from harness.api import BacktestResult, Fill, Regime, StageResult, TradeRecord

T0 = datetime(2020, 1, 2, tzinfo=UTC)
T1 = datetime(2020, 1, 3, tzinfo=UTC)
CAPITAL = CapitalConfig(amount=Decimal("10000"), effective_at=T0, source="default")


def _trade(**kw: object) -> TradeRecord:
    base: dict[str, object] = {
        "symbol": "SPY",
        "entry_ts": T0,
        "exit_ts": T1,
        "entry_price": 100.0,
        "exit_price": 101.0,
        "shares": 10.0,
        "cost": 1.0,
        "pnl": 9.0,
    }
    return TradeRecord(**{**base, **kw})


def test_trade_exit_cannot_precede_entry() -> None:
    with pytest.raises(ValidationError):
        _trade(entry_ts=T1, exit_ts=T0)


def test_trade_requires_positive_shares_and_prices() -> None:
    with pytest.raises(ValidationError):
        _trade(shares=0.0)
    with pytest.raises(ValidationError):
        _trade(entry_price=-1.0)


def test_trade_event_flag_defaults_to_false_and_can_be_set() -> None:
    assert _trade().crossed_event is False
    assert _trade(crossed_event=True).crossed_event is True


def test_trade_rejects_naive_datetimes() -> None:
    with pytest.raises(ValidationError):
        _trade(entry_ts=datetime(2020, 1, 2))


def test_regime_requires_end_after_start() -> None:
    with pytest.raises(ValidationError):
        Regime(name="x", start=T1, end=T0)


def _result(**kw: object) -> BacktestResult:
    base: dict[str, object] = {
        "capital": CAPITAL,
        "snapshot_id": "abc",
        "cost_multiplier": 1.0,
        "seed": 7,
        "code_version": "deadbeef",
        "survivorship_biased": True,
        "timestamps": (T0, T1),
        "daily_returns": (0.01, -0.005),
        "trades": (_trade(),),
    }
    return BacktestResult(**{**base, **kw})


def test_backtest_result_is_immutable() -> None:
    with pytest.raises(ValidationError):
        _result().seed = 1  # type: ignore[misc]


def test_returns_must_align_with_timestamps() -> None:
    with pytest.raises(ValidationError):
        _result(daily_returns=(0.01,))


def test_timestamps_must_be_strictly_increasing() -> None:
    with pytest.raises(ValidationError):
        _result(timestamps=(T1, T0))


def test_survivorship_flag_has_no_default() -> None:
    kwargs: dict[str, object] = {
        "capital": CAPITAL,
        "snapshot_id": "abc",
        "cost_multiplier": 1.0,
        "seed": 7,
        "code_version": "x",
        "timestamps": (T0,),
        "daily_returns": (0.0,),
        "trades": (),
    }
    with pytest.raises(ValidationError):
        BacktestResult(**kwargs)


def test_cost_multiplier_must_be_at_least_one() -> None:
    with pytest.raises(ValidationError):
        _result(cost_multiplier=0.5)


def test_stage_result_metrics_are_read_only() -> None:
    r = StageResult(stage=Stage.FLOORS, passed=False, reason="too few trades", metrics={"n": 3.0})
    with pytest.raises(TypeError):
        r.metrics["n"] = 4.0  # type: ignore[index]


def test_stage_result_failure_needs_a_reason() -> None:
    with pytest.raises(ValidationError):
        StageResult(stage=Stage.FLOORS, passed=False, reason="", metrics={})


def test_fill_requires_positive_shares_and_price() -> None:
    with pytest.raises(ValidationError):
        Fill(symbol="SPY", side="buy", shares=0.0, price=100.0, ts=T0)
