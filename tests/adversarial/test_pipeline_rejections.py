"""Task 43: each adversary is rejected at EXACTLY its expected stage, and the registry says so.

Earlier or later is a failure: dying early would not prove the expected defence works. These
tests call the public `run_pipeline` (harness.validation.pipeline), which always uses the
frozen default thresholds. Nothing here may be tuned to make a case pass; if a case lands at
the wrong stage, report it.
"""

from collections.abc import Callable, Mapping, Sequence
from typing import Any

from contracts import CapitalConfig, Outcome, Stage, Strategy
from harness.registry.sqlite import SqliteRegistry
from harness.validation.pipeline import run_pipeline
from tests.adversarial._sim import best_of_n
from tests.adversarial.strategies.cost_blind import DEFAULT_LOOKBACK as COST_BLIND_LOOKBACK
from tests.adversarial.strategies.cost_blind import CostBlindReversal
from tests.adversarial.strategies.leaky import LeakyStrategy
from tests.adversarial.strategies.noise import NoiseStrategy
from tests.adversarial.strategies.param_peak import ParamPeakStrategy
from tests.adversarial.strategies.reversal import ReversalStrategy
from tests.adversarial.strategies.single_regime import SingleRegimeWinner
from tests.adversarial.worlds import (
    PEAK_PERIOD,
    World,
    cost_blind_world,
    noise_world,
    param_peak_world,
    planted_edge_world,
    regime_world,
)

SEED = 0
SINGLE_REGIME_SEED = 26  # chosen for a world property (neighbour stability, 0.94), not a stage
N_NOISE_TRIALS = 60
LOOKBACK = 15
TOP_N = 3  # fixed inside `build` so stability varies only the lookback

Build = Callable[[Mapping[str, Any]], Strategy]


def _capital(world: World) -> CapitalConfig:
    return CapitalConfig(effective_at=world.timestamps[0], source="default")


def _submit(
    world: World,
    registry: SqliteRegistry,
    strategy: Strategy,
    build: Build,
    params: Mapping[str, int | float],
) -> Any:
    return run_pipeline(
        strategy=strategy,
        build=build,
        params=params,
        provider=world.provider(),
        symbols=list(world.symbols),
        start=world.timestamps[0],
        end=world.timestamps[-1],
        capital=_capital(world),
        registry=registry,
        seed=SEED,
    )


def _trace(result: Any) -> list[tuple[Stage, bool, str]]:
    return [(s.stage, s.passed, s.reason) for s in result.stages]


def _assert_rejected_at(result: Any, registry: SqliteRegistry, stage: Stage) -> None:
    assert result.outcome == Outcome.REJECTED, _trace(result)
    assert result.stage_reached == stage, _trace(result)
    assert result.stages[-1].stage == stage
    assert not result.stages[-1].passed
    assert all(s.passed for s in result.stages[:-1])
    recorded = registry.get(result.trial.id)
    assert recorded is not None
    assert recorded.stage_reached == stage
    assert recorded.outcome == Outcome.REJECTED


def _reversal_build(symbols: Sequence[str]) -> Build:
    return lambda p: ReversalStrategy(symbols, lookback=int(p["lookback"]), top_n=TOP_N)


def test_leaky_strategy_is_rejected_at_lookahead():
    world = noise_world(1)
    registry = SqliteRegistry()
    result = _submit(
        world, registry, LeakyStrategy(world.symbols), lambda p: LeakyStrategy(world.symbols), {}
    )
    _assert_rejected_at(result, registry, Stage.LOOKAHEAD)


def test_best_of_n_noise_is_rejected_at_trial_adjusted():
    world = noise_world(7, symbols=tuple(f"S{i:02d}" for i in range(6)))
    registry = SqliteRegistry()

    def noise_build(p: Mapping[str, Any]) -> Strategy:
        return NoiseStrategy(world.symbols, int(p["seed"]))

    best_seed, _ = best_of_n(world, N_NOISE_TRIALS)
    for seed in range(N_NOISE_TRIALS):
        if seed != best_seed:  # every other trial is logged before the winner is judged
            _submit(world, registry, noise_build({"seed": seed}), noise_build, {"seed": seed})
    result = _submit(
        world, registry, noise_build({"seed": best_seed}), noise_build, {"seed": best_seed}
    )
    assert registry.count() == N_NOISE_TRIALS
    _assert_rejected_at(result, registry, Stage.TRIAL_ADJUSTED)


def test_cost_blind_edge_is_rejected_at_cost_sensitivity():
    world = cost_blind_world(2)
    registry = SqliteRegistry()
    result = _submit(
        world,
        registry,
        CostBlindReversal(world.symbols, lookback=COST_BLIND_LOOKBACK),
        lambda p: CostBlindReversal(world.symbols, lookback=int(p["lookback"])),
        {"lookback": COST_BLIND_LOOKBACK},
    )
    _assert_rejected_at(result, registry, Stage.COST_SENSITIVITY)


def test_single_regime_winner_is_rejected_at_floors():
    world = regime_world(SINGLE_REGIME_SEED)
    registry = SqliteRegistry()
    result = _submit(
        world,
        registry,
        SingleRegimeWinner(world.symbols, lookback=LOOKBACK),
        lambda p: SingleRegimeWinner(world.symbols, lookback=int(p["lookback"])),
        {"lookback": LOOKBACK},
    )
    _assert_rejected_at(result, registry, Stage.FLOORS)


def test_param_peak_overfit_is_rejected_at_stability():
    world = param_peak_world(3)
    registry = SqliteRegistry()
    result = _submit(
        world,
        registry,
        ParamPeakStrategy(world.symbols, period=PEAK_PERIOD),
        lambda p: ParamPeakStrategy(world.symbols, period=int(p["period"])),
        {"period": PEAK_PERIOD},
    )
    _assert_rejected_at(result, registry, Stage.STABILITY)


def test_positive_control_planted_edge_passes_every_stage():
    world = planted_edge_world(0)
    registry = SqliteRegistry()
    result = _submit(
        world,
        registry,
        ReversalStrategy(world.symbols, lookback=LOOKBACK, top_n=TOP_N),
        _reversal_build(world.symbols),
        {"lookback": LOOKBACK},
    )
    assert result.outcome == Outcome.PASSED, _trace(result)
    assert [s.stage for s in result.stages] == [
        Stage.LOOKAHEAD,
        Stage.WALK_FORWARD,
        Stage.TRIAL_ADJUSTED,
        Stage.COST_SENSITIVITY,
        Stage.STABILITY,
        Stage.FLOORS,
    ]
    assert all(s.passed for s in result.stages)
    recorded = registry.get(result.trial.id)
    assert recorded is not None
    assert recorded.outcome == Outcome.PASSED
    assert recorded.stage_reached == Stage.FLOORS


def test_every_call_leaves_exactly_one_registry_entry():
    world = noise_world(1)
    registry = SqliteRegistry()
    _submit(
        world, registry, LeakyStrategy(world.symbols), lambda p: LeakyStrategy(world.symbols), {}
    )
    assert registry.count() == 1
