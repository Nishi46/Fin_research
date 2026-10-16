from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta

import numpy as np
import pytest

from contracts import CapitalConfig, Stage
from harness.api import BacktestResult, TradeRecord
from harness.thresholds import Thresholds
from harness.validation.cost_sensitivity import cost_sensitivity_stage
from harness.validation.stability import stability_stage

START = datetime(2022, 1, 3, tzinfo=UTC)


def make_result(
    pnls: list[float], returns: list[float] | None = None, multiplier: float = 1.0
) -> BacktestResult:
    n = len(returns) if returns is not None else 50
    ts = tuple(START + timedelta(days=i) for i in range(n))
    trades = tuple(
        TradeRecord(
            symbol="X",
            entry_ts=START,
            exit_ts=START + timedelta(days=1),
            entry_price=10,
            exit_price=10,
            shares=1,
            cost=0,
            pnl=p,
        )
        for p in pnls
    )
    return BacktestResult(
        capital=CapitalConfig(effective_at=START, source="default"),
        snapshot_id="s",
        cost_multiplier=multiplier,
        seed=0,
        code_version="v",
        survivorship_biased=True,
        timestamps=ts,
        daily_returns=tuple(returns if returns is not None else [0.0] * n),
        trades=trades,
    )


# --- cost sensitivity ------------------------------------------------------------------------


def test_passes_when_profit_factor_survives_doubled_costs() -> None:
    base = make_result([3.0, 3.0, -1.0])  # PF 6
    seen: list[float] = []

    def run(multiplier: float) -> BacktestResult:
        seen.append(multiplier)
        return make_result([2.0, 2.0, -1.5], multiplier=multiplier)  # PF 2.67

    verdict = cost_sensitivity_stage(base, run)
    assert verdict.stage is Stage.COST_SENSITIVITY
    assert verdict.passed, verdict.reason
    assert seen == [Thresholds().cost_multiplier]
    assert verdict.metrics["profit_factor_base"] == pytest.approx(6.0)
    assert verdict.metrics["profit_factor_scaled"] == pytest.approx(4.0 / 1.5)


def test_fails_when_profit_vanishes_under_doubled_costs() -> None:
    base = make_result([2.0, 2.0, -1.0])
    verdict = cost_sensitivity_stage(
        base,
        lambda m: make_result([0.5, 0.5, -1.5], multiplier=m),  # PF 0.67
    )
    assert not verdict.passed
    assert "profit factor" in verdict.reason
    assert verdict.metrics["profit_factor_scaled"] < 1.0


def test_no_trades_after_scaling_fails() -> None:
    verdict = cost_sensitivity_stage(
        make_result([1.0, -0.5]), lambda m: make_result([], multiplier=m)
    )
    assert not verdict.passed


def test_uses_injected_thresholds() -> None:
    base = make_result([2.0, -1.0])

    def run(m: float) -> BacktestResult:
        return make_result([1.2, -1.0], multiplier=m)  # PF 1.2

    assert cost_sensitivity_stage(base, run).passed
    strict = Thresholds(cost_multiplier=3.0, cost_min_profit_factor=1.5)
    seen: list[float] = []

    def run3(m: float) -> BacktestResult:
        seen.append(m)
        return make_result([1.2, -1.0], multiplier=m)

    assert not cost_sensitivity_stage(base, run3, strict).passed
    assert seen == [3.0]


def test_a_runner_that_ignores_the_multiplier_is_caught() -> None:
    base = make_result([2.0, -1.0])
    verdict = cost_sensitivity_stage(base, lambda _m: make_result([2.0, -1.0], multiplier=1.0))
    assert not verdict.passed
    assert "multiplier" in verdict.reason


# --- stability -------------------------------------------------------------------------------

Params = Mapping[str, int]


def returns_with_sharpe(level: float, n: int = 252) -> list[float]:
    rng = np.random.default_rng(0)
    noise = rng.normal(0.0, 0.01, n)
    noise = (noise - noise.mean()) / noise.std(ddof=1) * 0.01
    return [float(x) for x in noise + level * 0.01 / np.sqrt(252)]


def run_by_level(
    levels: dict[int, float],
) -> tuple[Callable[[Params], int], Callable[[int], BacktestResult]]:
    def build(params: Params) -> int:
        return params["lookback"]

    def run(strategy: int) -> BacktestResult:
        return make_result([1.0], returns_with_sharpe(levels[strategy]))

    return build, run


POINTS = [{"lookback": 4}, {"lookback": 5}, {"lookback": 6}]


def test_flat_plateau_is_stable() -> None:
    build, run = run_by_level({4: 1.9, 5: 2.0, 6: 1.95})
    verdict = stability_stage(POINTS, build, run)
    assert verdict.stage is Stage.STABILITY
    assert verdict.passed, verdict.reason
    assert verdict.metrics["n_points"] == 3
    assert verdict.metrics["peak_sharpe"] == pytest.approx(2.0, rel=0.02)


def test_single_lucky_peak_fails() -> None:
    build, run = run_by_level({4: 0.2, 5: 2.0, 6: 0.3})
    verdict = stability_stage(POINTS, build, run)
    assert not verdict.passed
    assert "peak" in verdict.reason
    assert verdict.metrics["min_over_peak"] < 0.2


def test_tolerance_is_injected() -> None:
    build, run = run_by_level({4: 1.5, 5: 2.0, 6: 1.9})
    assert not stability_stage(POINTS, build, run, Thresholds(stability_tolerance=0.1)).passed
    assert stability_stage(POINTS, build, run, Thresholds(stability_tolerance=0.3)).passed


def test_non_positive_peak_fails() -> None:
    build, run = run_by_level({4: -1.0, 5: -0.5, 6: -2.0})
    assert not stability_stage(POINTS, build, run).passed


def test_needs_neighbours() -> None:
    build, run = run_by_level({5: 2.0})
    verdict = stability_stage([{"lookback": 5}], build, run)
    assert not verdict.passed
    assert "neighbour" in verdict.reason
