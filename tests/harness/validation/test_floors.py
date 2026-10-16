from datetime import UTC, datetime, timedelta

from contracts import CapitalConfig, Stage
from harness.api import BacktestResult, Regime, TradeRecord
from harness.thresholds import DEFAULT_THRESHOLDS, Thresholds
from harness.validation.floors import floors_stage

START = datetime(2018, 2, 1, tzinfo=UTC)
REGIMES = (
    Regime(name="r1", start=datetime(2018, 1, 1, tzinfo=UTC), end=datetime(2018, 7, 1, tzinfo=UTC)),
    Regime(name="r2", start=datetime(2018, 7, 1, tzinfo=UTC), end=datetime(2019, 1, 1, tzinfo=UTC)),
    Regime(name="r3", start=datetime(2019, 1, 1, tzinfo=UTC), end=datetime(2019, 7, 1, tzinfo=UTC)),
)
SMALL = Thresholds(min_trades=3, min_profitable_regimes=2)


def trade(day: int, pnl: float) -> TradeRecord:
    t = START + timedelta(days=day)
    return TradeRecord(
        symbol="X", entry_ts=t, exit_ts=t, entry_price=10, exit_price=10, shares=1, cost=0, pnl=pnl
    )


def result(trades: list[TradeRecord], n_days: int = 400, daily: float = 0.001) -> BacktestResult:
    ts = tuple(START + timedelta(days=i) for i in range(n_days))
    return BacktestResult(
        capital=CapitalConfig(effective_at=START, source="default"),
        snapshot_id="s",
        cost_multiplier=1.0,
        seed=0,
        code_version="v",
        survivorship_biased=True,
        timestamps=ts,
        daily_returns=tuple(daily for _ in ts),
        trades=tuple(trades),
    )


def test_enough_trades_and_regimes_pass() -> None:
    trades = [trade(10, 5.0), trade(200, 5.0), trade(350, 5.0)]
    verdict = floors_stage(result(trades), REGIMES, SMALL)
    assert verdict.stage is Stage.FLOORS
    assert verdict.passed, verdict.reason
    assert verdict.metrics["profitable_regimes"] == 3


def test_tiny_sample_fails_and_reports_no_sharpe() -> None:
    verdict = floors_stage(result([trade(10, 5.0), trade(20, 5.0)]), REGIMES, SMALL)
    assert not verdict.passed
    assert "trades" in verdict.reason
    assert verdict.metrics["n_trades"] == 2
    assert not any("sharpe" in k for k in verdict.metrics)


def test_zero_trades_fails() -> None:
    assert not floors_stage(result([]), REGIMES, SMALL).passed


def test_single_regime_winner_fails_even_with_many_trades() -> None:
    trades = [trade(d, 5.0) for d in range(5, 60, 10)]  # all inside r1
    verdict = floors_stage(result(trades), REGIMES, SMALL)
    assert not verdict.passed
    assert "regime" in verdict.reason
    assert verdict.metrics["profitable_regimes"] == 1


def test_losing_regimes_do_not_count() -> None:
    trades = [trade(10, 5.0), trade(200, -9.0), trade(300, -9.0), trade(305, 1.0)]
    verdict = floors_stage(result(trades, daily=-0.001), REGIMES, SMALL)
    assert not verdict.passed
    assert verdict.metrics["profitable_regimes"] == 1


def test_thresholds_are_read_from_the_config_not_hard_coded() -> None:
    trades = [trade(10, 5.0), trade(200, 5.0), trade(350, 5.0)]
    strict = Thresholds(min_trades=4, min_profitable_regimes=2)
    assert not floors_stage(result(trades), REGIMES, strict).passed
    assert floors_stage(result(trades), REGIMES, SMALL).passed
    # Default config: 3 trades is far below the default floor.
    assert not floors_stage(result(trades), REGIMES).passed
    assert DEFAULT_THRESHOLDS.min_trades > 3


def test_trades_outside_every_regime_are_counted_not_enforced() -> None:
    inside = [trade(10, 5.0), trade(200, 5.0), trade(350, 5.0)]
    outside = [trade(900, 5.0), trade(950, -1.0)]  # exits in 2020, after the table ends
    verdict = floors_stage(result([*inside, *outside]), REGIMES, SMALL)
    assert verdict.metrics["trades_outside_regimes"] == 2
    assert verdict.passed  # flagged, not enforced
    assert floors_stage(result(inside), REGIMES, SMALL).metrics["trades_outside_regimes"] == 0


def test_trades_outside_regimes_is_reported_on_a_failing_sample_too() -> None:
    verdict = floors_stage(result([trade(900, 5.0)]), REGIMES, SMALL)
    assert not verdict.passed
    assert verdict.metrics["trades_outside_regimes"] == 1


def test_number_of_regimes_is_reported() -> None:
    trades = [trade(10, 5.0), trade(200, 5.0), trade(350, 5.0)]
    assert floors_stage(result(trades), REGIMES, SMALL).metrics["n_regimes"] == 3
    assert floors_stage(result([trade(10, 1.0)]), REGIMES[:2], SMALL).metrics["n_regimes"] == 2


def test_the_regimes_passed_in_decide_the_result() -> None:
    trades = [trade(10, 5.0), trade(200, 5.0), trade(350, 5.0)]
    assert floors_stage(result(trades), REGIMES, SMALL).passed
    assert not floors_stage(result(trades), REGIMES[:1], SMALL).passed  # only one regime to win
    assert floors_stage(result(trades), (), SMALL).metrics["trades_outside_regimes"] == 3
