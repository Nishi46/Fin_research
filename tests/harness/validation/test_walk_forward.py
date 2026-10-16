from datetime import UTC, datetime, timedelta

import numpy as np

from contracts import CapitalConfig, Stage
from harness.api import BacktestResult
from harness.thresholds import Thresholds
from harness.validation.walk_forward import walk_forward_stage

START = datetime(2021, 1, 4, tzinfo=UTC)


def result_from(returns: list[float]) -> BacktestResult:
    ts = tuple(START + timedelta(days=i) for i in range(len(returns)))
    return BacktestResult(
        capital=CapitalConfig(effective_at=START, source="default"),
        snapshot_id="s",
        cost_multiplier=1.0,
        seed=0,
        code_version="v",
        survivorship_biased=True,
        timestamps=ts,
        daily_returns=tuple(returns),
        trades=(),
    )


def noisy(mean: float, n: int = 600, seed: int = 0) -> list[float]:
    return [float(x) for x in np.random.default_rng(seed).normal(mean, 0.01, n)]


def test_consistent_edge_passes() -> None:
    verdict = walk_forward_stage(result_from(noisy(0.003)))
    assert verdict.stage is Stage.WALK_FORWARD
    assert verdict.passed, verdict.reason
    assert verdict.metrics["n_folds"] == 5
    assert verdict.metrics["positive_fold_fraction"] >= 0.6
    assert verdict.metrics["oos_sharpe"] > 0


def test_losing_series_fails() -> None:
    verdict = walk_forward_stage(result_from(noisy(-0.003)))
    assert not verdict.passed
    assert "Sharpe" in verdict.reason


def test_profit_from_a_single_fold_fails_the_fold_fraction() -> None:
    # Fold test windows are the last 5 of 6 equal blocks; only the first of them wins, hugely.
    returns = [-0.001] * 600
    for i in range(100, 200):
        returns[i] = 0.05
    verdict = walk_forward_stage(result_from(returns))
    assert verdict.metrics["oos_sharpe"] > 0  # pooled is positive...
    assert verdict.metrics["positive_fold_fraction"] == 0.2  # ...but one fold of five carries it
    assert not verdict.passed
    assert "fold" in verdict.reason


def test_sample_too_short_for_the_splits_fails_closed() -> None:
    verdict = walk_forward_stage(result_from(noisy(0.003, n=4)))
    assert not verdict.passed
    assert "too short" in verdict.reason


def test_thresholds_are_injected() -> None:
    result = result_from(noisy(0.0, seed=3))
    assert walk_forward_stage(
        result, Thresholds(wf_min_oos_sharpe=-50.0, wf_min_positive_fold_fraction=0.2)
    ).passed
    assert not walk_forward_stage(result, Thresholds(wf_min_oos_sharpe=50.0)).passed
    assert (
        walk_forward_stage(
            result,
            Thresholds(wf_n_splits=3, wf_min_oos_sharpe=-50.0, wf_min_positive_fold_fraction=0.2),
        ).metrics["n_folds"]
        == 3
    )
