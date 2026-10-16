"""Task 37: the leaky strategy really peeks, and an honest one really does not."""

from datetime import timedelta

import pytest

from tests.adversarial._sim import signals_differ_under_truncation, simulate
from tests.adversarial.strategies.leaky import LeakyStrategy
from tests.adversarial.strategies.reversal import ReversalStrategy
from tests.adversarial.worlds import noise_world

CHECK_DAYS = list(range(40, 400, 29))


@pytest.fixture(scope="module")
def world():
    return noise_world(1)


def test_leaky_signal_changes_when_data_is_truncated(world):
    strategy = LeakyStrategy(world.symbols)
    differing = signals_differ_under_truncation(strategy, world, CHECK_DAYS)
    assert len(differing) == len(CHECK_DAYS)


def test_honest_control_is_unchanged_by_truncation(world):
    strategy = ReversalStrategy(world.symbols, lookback=10, top_n=2)
    assert signals_differ_under_truncation(strategy, world, CHECK_DAYS) == []


def test_leak_is_what_makes_it_look_brilliant_on_pure_noise(world):
    result = simulate(world, LeakyStrategy(world.symbols), end=300)
    assert result.annual_sharpe(result.gross) > 10
    honest = simulate(world, ReversalStrategy(world.symbols), end=300)
    assert abs(honest.annual_sharpe(honest.gross)) < 3


def test_leaky_reads_beyond_the_view_clock(world):
    asof = world.timestamps[100]
    view_provider = world.provider(upto=world.timestamps[-1])
    future = view_provider.bars(world.symbols, asof + timedelta(days=5))
    assert any(b.ts > asof for b in future)  # the data it steals really is from the future
