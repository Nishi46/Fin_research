"""Task 40: a winner whose profit all comes from one derived regime.

Regimes are derived from the world's benchmark (never hand-listed). The edge sits in the final
derived advance; every other derived regime carries a momentum tendency, which a reversal
strategy loses to.
"""

import pytest

from contracts import CapitalConfig
from harness.engine.runner import run_backtest
from harness.metrics.core import sharpe
from harness.thresholds import DEFAULT_THRESHOLDS
from tests.adversarial._regimes import pnl_by_regime
from tests.adversarial._sim import simulate
from tests.adversarial.strategies.single_regime import SingleRegimeWinner
from tests.adversarial.worlds import ASSUMED_COST_BPS_PER_SIDE, regime_world

# Seeds were chosen for WORLD properties only (neighbour stability, exactly one profitable
# derived regime, significance); 48 seeds were scanned and 7 qualified. No stage was consulted.
SEEDS = [26, 27, 29]
NEIGHBOUR_LOOKBACKS = (12, 14, 16, 18)


@pytest.fixture(scope="module", params=SEEDS)
def run(request):
    world = regime_world(request.param)
    capital = CapitalConfig(effective_at=world.timestamps[0], source="default")
    backtest = run_backtest(
        provider=world.provider(),
        strategy=SingleRegimeWinner(world.symbols),
        symbols=list(world.symbols),
        start=world.timestamps[0],
        end=world.timestamps[-1],
        capital=capital,
    )
    sim = simulate(world, SingleRegimeWinner(world.symbols), cost_bps=ASSUMED_COST_BPS_PER_SIDE)
    return world, backtest, sim


def test_edge_regime_is_chosen_structurally(run):
    world, _, _ = run
    assert world.edge_regime == world.regimes[-1]
    assert world.edge_regime.name.startswith("advance_")


def test_profitable_in_exactly_one_derived_regime(run):
    world, backtest, _ = run
    pnl = pnl_by_regime(backtest, world.regimes)
    assert [name for name, value in pnl.items() if value > 0] == [world.edge_regime.name]
    assert len(world.regimes) > DEFAULT_THRESHOLDS.min_profitable_regimes - 1


def test_overall_winner_with_enough_trades(run):
    _, backtest, sim = run
    assert len(backtest.trades) >= DEFAULT_THRESHOLDS.min_trades * 3
    assert sim.net_mean > 0
    assert sim.t_stat(sim.net) > 3


def test_world_is_stable_across_neighbouring_lookbacks(run):
    """It must die at floors, so it may not be fragile enough to die at stability first."""
    world, backtest, _ = run
    capital = CapitalConfig(effective_at=world.timestamps[0], source="default")
    scores = [sharpe(backtest.daily_returns)]
    for lookback in NEIGHBOUR_LOOKBACKS:
        result = run_backtest(
            provider=world.provider(),
            strategy=SingleRegimeWinner(world.symbols, lookback=lookback),
            symbols=list(world.symbols),
            start=world.timestamps[0],
            end=world.timestamps[-1],
            capital=capital,
        )
        scores.append(sharpe(result.daily_returns))
    assert min(scores) / max(scores) >= 1.0 - DEFAULT_THRESHOLDS.stability_tolerance
