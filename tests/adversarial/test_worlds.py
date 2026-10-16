"""Task 36: the synthetic worlds really contain (or lack) the structure they claim."""

import numpy as np
import pytest

from harness.data.regimes import derive_regimes
from harness.thresholds import DEFAULT_THRESHOLDS
from tests.adversarial.worlds import (
    SIGNAL_WINDOW,
    World,
    noise_world,
    planted_edge_world,
    regime_world,
)

SEEDS = range(5)


def _predictor_corr(world: World, lo: int = 0, hi: int | None = None) -> float:
    """Correlation of next-day log return with the trailing-window mean return."""
    lr = np.diff(np.log(world.closes), axis=0)  # lr[i] is the return into day i + 1
    hi = len(lr) if hi is None else min(hi, len(lr))
    alpha = 2.0 / (SIGNAL_WINDOW + 1)
    carry = np.zeros_like(lr)
    for i in range(len(lr)):
        carry[i] = alpha * lr[i] + (1 - alpha) * (carry[i - 1] if i else 0.0)
    xs, ys = [], []
    for i in range(max(lo, SIGNAL_WINDOW), hi - 1):
        xs.append(carry[i])
        ys.append(lr[i + 1])
    return float(np.corrcoef(np.concatenate(xs), np.concatenate(ys))[0, 1])


def test_worlds_are_deterministic_per_seed():
    a, b = planted_edge_world(3), planted_edge_world(3)
    assert a.bars == b.bars
    assert a.provider().snapshot().id == b.provider().snapshot().id
    assert planted_edge_world(4).bars != a.bars


def test_bars_are_utc_weekdays_and_valid():
    world = noise_world(0)
    assert all(ts.tzinfo is not None and ts.weekday() < 5 for ts in world.timestamps)
    assert len(world.bars) == len(world.timestamps) * len(world.symbols)


@pytest.mark.parametrize("seed", SEEDS)
def test_noise_world_has_no_edge(seed):
    world = noise_world(seed)
    assert abs(_predictor_corr(world)) < 0.04
    lr = np.diff(np.log(world.closes), axis=0)
    t_stat = lr.mean() / (lr.std() / np.sqrt(lr.size))
    assert abs(t_stat) < 3.5


@pytest.mark.parametrize("seed", SEEDS)
def test_planted_world_has_strong_reversal_edge(seed):
    assert _predictor_corr(planted_edge_world(seed)) < -0.1


@pytest.mark.parametrize("seed", SEEDS)
def test_regime_world_edge_only_in_the_edge_regime(seed):
    world = regime_world(seed)
    assert world.edge_regime is not None
    lo, hi = world.day_range(world.edge_regime)
    assert _predictor_corr(world, lo, hi) < -0.1
    for regime in world.regimes:
        if regime != world.edge_regime:
            a, b = world.day_range(regime)
            assert _predictor_corr(world, a, b) > -0.05


@pytest.mark.parametrize("seed", range(10))
@pytest.mark.parametrize("factory", [regime_world, planted_edge_world, noise_world])
def test_benchmark_derives_advance_selloff_advance(factory, seed):
    world = factory(seed)
    kinds = [r.name.split("_")[0] for r in world.regimes]
    assert kinds == ["advance", "selloff", "advance"], [r.name for r in world.regimes]
    bench = {b.ts: b.close for b in world.benchmark_bars}
    selloff = world.regimes[1]
    closes = [c for ts, c in bench.items() if selloff.start <= ts <= selloff.end]
    assert min(closes) / max(closes) < 0.75  # a clear fall, not borderline at 15%


def test_benchmark_is_served_but_not_tradable():
    world = regime_world(0)
    benchmark = DEFAULT_THRESHOLDS.benchmark_symbol
    assert benchmark not in world.symbols
    assert all(b.symbol != benchmark for b in world.bars)
    served = world.provider().bars([benchmark], world.timestamps[-1])
    assert {b.ts for b in served} == set(world.timestamps)


def test_regimes_are_the_derived_ones():
    world = regime_world(0)
    assert world.regimes == derive_regimes(
        world.benchmark_bars, min_drawdown=DEFAULT_THRESHOLDS.regime_min_drawdown
    )


def test_regime_world_is_deterministic_in_its_benchmark():
    assert regime_world(2).benchmark_bars == regime_world(2).benchmark_bars
    assert regime_world(3).benchmark_bars != regime_world(2).benchmark_bars


def test_truncated_provider_hides_the_future():
    world = noise_world(0)
    cut = world.timestamps[100]
    assert (
        max(b.ts for b in world.provider(upto=cut).bars(world.symbols, world.timestamps[-1])) == cut
    )
