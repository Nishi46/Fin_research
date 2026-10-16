"""A genuine planted edge, honestly traded, is profitable everywhere (the positive control)."""

import pytest

from contracts import CapitalConfig
from harness.engine.runner import run_backtest
from harness.thresholds import DEFAULT_THRESHOLDS
from tests.adversarial._regimes import pnl_by_regime
from tests.adversarial._sim import simulate
from tests.adversarial.strategies.reversal import ReversalStrategy
from tests.adversarial.worlds import ASSUMED_COST_BPS_PER_SIDE, planted_edge_world


@pytest.fixture(scope="module")
def run():
    world = planted_edge_world(0)
    return world, simulate(
        world,
        ReversalStrategy(world.symbols),
        cost_bps=ASSUMED_COST_BPS_PER_SIDE,
    )


def test_strong_and_significant_after_costs(run):
    _, r = run
    assert r.annual_sharpe(r.net) > 2.0
    assert r.t_stat(r.net) > 5
    assert r.profit_factor > 1.2


def test_profitable_in_every_derived_regime_including_the_selloff():
    world = planted_edge_world(0)
    capital = CapitalConfig(effective_at=world.timestamps[0], source="default")
    result = run_backtest(
        provider=world.provider(),
        strategy=ReversalStrategy(world.symbols),
        symbols=list(world.symbols),
        start=world.timestamps[0],
        end=world.timestamps[-1],
        capital=capital,
    )
    pnl = pnl_by_regime(result, world.regimes)
    assert len(world.regimes) >= DEFAULT_THRESHOLDS.min_profitable_regimes
    assert any(name.startswith("selloff_") for name in pnl)
    assert all(value > 0 for value in pnl.values()), pnl


def test_neighbouring_lookbacks_hold_up():
    world = planted_edge_world(0)
    base = simulate(world, ReversalStrategy(world.symbols, lookback=15), cost_bps=5.0, end=500)
    for lb in (12, 14, 16, 18):
        other = simulate(world, ReversalStrategy(world.symbols, lookback=lb), cost_bps=5.0, end=500)
        assert other.net_mean > 0.8 * base.net_mean
