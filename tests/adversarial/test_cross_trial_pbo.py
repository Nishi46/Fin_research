"""Cross-trial PBO against the adversaries.

A candidate is judged against the other trials of its group (same data snapshot, window, costs and
capital). The peers here are real backtests of noise strategies, filed exactly as the pipeline files
them (only those that clear walk-forward reach stage 3), and every submitted peer is also logged as
a trial so DSR deflates by the true N. The candidate then goes through the public `run_pipeline`
with the frozen default thresholds.

What the statistics support, and what these tests therefore assert:

* A planted edge among noise has a persistently better Sharpe in every block, so the in-sample
  winner stays above the median out of sample: PBO is near zero.
* The best of many pure-noise strategies has no persistent edge, so PBO scatters around 0.5. It is
  NOT reliably above the maximum on any single draw; DSR is what reliably rejects best-of-N noise.
  The tests assert that noise never looks edge-like and that the gate rejects when PBO is above
  the maximum, not that every noise draw is rejected by PBO.
"""

import uuid
from collections.abc import Callable, Mapping, Sequence
from typing import Any

import numpy as np
import pytest

from contracts import CapitalConfig, Outcome, ReturnSeries, Stage, Strategy
from fakes.registry import InMemoryRegistry
from harness.api import BacktestResult
from harness.engine.runner import run_backtest
from harness.thresholds import DEFAULT_THRESHOLDS
from harness.validation.pbo import probability_of_backtest_overfitting
from harness.validation.peers import cost_model_fingerprint, group_key
from harness.validation.pipeline import PipelineResult, run_pipeline
from harness.validation.walk_forward import walk_forward_stage
from tests.adversarial._sim import best_of_n
from tests.adversarial.strategies.noise import NoiseStrategy
from tests.adversarial.strategies.reversal import ReversalStrategy
from tests.adversarial.worlds import World, noise_world, planted_edge_world
from tests.helpers import make_trial, research_provider

SYMBOLS = tuple(f"S{i:02d}" for i in range(6))
N_NOISE = 60  # for the multi-seed statistic
N_NOISE_GATE = 100  # about 40% clear walk-forward, so this is what it takes to reach 30 peers
LOOKBACK = 15
TOP_N = 3


def _capital(world: World) -> CapitalConfig:
    return CapitalConfig(effective_at=world.timestamps[0], source="default")


def _backtest(world: World, strategy: Strategy) -> BacktestResult:
    return run_backtest(
        provider=world.provider(),
        strategy=strategy,
        symbols=list(world.symbols),
        start=world.timestamps[0],
        end=world.timestamps[-1],
        capital=_capital(world),
    )


def _noise_results(world: World, n: int, skip: int | None = None) -> list[BacktestResult]:
    return [
        _backtest(world, NoiseStrategy(world.symbols, seed)) for seed in range(n) if seed != skip
    ]


def _file_as_the_pipeline_does(
    world: World, registry: InMemoryRegistry, results: Sequence[BacktestResult]
) -> int:
    """Log every result as a trial; file the series of those reaching stage 3 (returns how many)."""
    filed = 0
    for result in results:
        returns = np.asarray(result.daily_returns)
        sharpe = float(returns.mean() / returns.std(ddof=1))
        registry.append(make_trial(id=uuid.uuid4(), metrics={"sharpe_per_period": sharpe}))
        if walk_forward_stage(result, DEFAULT_THRESHOLDS).passed:
            registry.append_returns(
                ReturnSeries(
                    trial_id=uuid.uuid4(),
                    group_key=group_key(
                        snapshot_id=result.snapshot_id,
                        start=world.timestamps[0],
                        end=world.timestamps[-1],
                        cost_multiplier=result.cost_multiplier,
                        capital_amount=result.capital.amount,
                        capital_currency=result.capital.currency,
                        cost_fingerprint=cost_model_fingerprint(None),
                    ),
                    timestamps=result.timestamps,
                    returns=result.daily_returns,
                )
            )
            filed += 1
    return filed


def _submit(
    world: World,
    registry: InMemoryRegistry,
    strategy: Strategy,
    build: Callable[[Mapping[str, Any]], Strategy],
    params: Mapping[str, int | float],
) -> PipelineResult:
    return run_pipeline(
        strategy=strategy,
        build=build,
        params=params,
        provider=research_provider(world.provider(), end=world.timestamps[-1]),
        symbols=list(world.symbols),
        start=world.timestamps[0],
        end=world.timestamps[-1],
        capital=_capital(world),
        registry=registry,
        seed=0,
    )


def _noise_pbo(seed: int) -> float:
    """PBO of the best-in-sample noise strategy against the others that reach stage 3."""
    world = noise_world(seed, symbols=SYMBOLS)
    results = [
        r
        for r in _noise_results(world, N_NOISE)
        if walk_forward_stage(r, DEFAULT_THRESHOLDS).passed
    ]
    columns = [np.asarray(r.daily_returns) for r in results]
    best = int(np.argmax([c.mean() / c.std(ddof=1) for c in columns]))
    matrix = np.column_stack([columns[best], *(c for i, c in enumerate(columns) if i != best)])
    return probability_of_backtest_overfitting(matrix, DEFAULT_THRESHOLDS.pbo_n_blocks)


def test_best_of_n_noise_never_looks_like_an_edge_across_seeds() -> None:
    values = [_noise_pbo(seed) for seed in range(4)]
    assert float(np.mean(values)) > 0.4, values
    assert min(values) > 0.1, values  # an edge-like PBO is near zero


def test_the_gate_rejects_best_of_n_noise_when_pbo_is_above_the_maximum() -> None:
    # Seed 2 is one of the draws where PBO lands above the maximum (about 0.69 of 4 draws that
    # exceeded 0.5 in the measurement); it is a fixed fixture, not a claim that every draw does.
    world = noise_world(2, symbols=SYMBOLS)
    registry = InMemoryRegistry()
    best_seed, _ = best_of_n(world, N_NOISE_GATE)
    filed = _file_as_the_pipeline_does(
        world, registry, _noise_results(world, N_NOISE_GATE, best_seed)
    )
    assert filed >= DEFAULT_THRESHOLDS.pbo_min_trials  # the gate is on
    result = _submit(
        world,
        registry,
        NoiseStrategy(world.symbols, best_seed),
        lambda p: NoiseStrategy(world.symbols, int(p["seed"])),
        {"seed": best_seed},
    )
    metrics = result.trial.metrics
    assert metrics["trial_adjusted.pbo_gating"] == 1.0
    assert metrics["trial_adjusted.pbo"] > DEFAULT_THRESHOLDS.pbo_max
    assert result.outcome is Outcome.REJECTED
    assert result.stage_reached is Stage.TRIAL_ADJUSTED
    assert "PBO" in result.stages[-1].reason
    # The rejected candidate's own series now counts toward the next trial.
    assert metrics["trial_adjusted.pbo_group_size"] == filed + 1


@pytest.mark.parametrize("seed", [0, 1])
def test_a_planted_edge_among_noise_passes_with_a_low_pbo(seed: int) -> None:
    world = planted_edge_world(seed, symbols=SYMBOLS)
    registry = InMemoryRegistry()
    filed = _file_as_the_pipeline_does(world, registry, _noise_results(world, N_NOISE_GATE))
    assert filed >= DEFAULT_THRESHOLDS.pbo_min_trials  # the gate is on

    def build(p: Mapping[str, Any]) -> Strategy:
        return ReversalStrategy(world.symbols, lookback=int(p["lookback"]), top_n=TOP_N)

    result = _submit(world, registry, build({"lookback": LOOKBACK}), build, {"lookback": LOOKBACK})
    metrics = result.trial.metrics
    assert metrics["trial_adjusted.pbo_gating"] == 1.0
    assert metrics["trial_adjusted.pbo"] < 0.1
    assert result.outcome is Outcome.PASSED, [(s.stage, s.reason) for s in result.stages]
