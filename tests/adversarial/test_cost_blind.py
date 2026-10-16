"""Task 39: an edge that is profitable at 1x costs and unprofitable at 2x, on the REAL cost model.

Measured with the harness backtester and `RealCostModel`, independent of the validation
stage, so the property holds whether or not `cost_sensitivity` fires. The world constants
are tuned to this cost scale only; no threshold is involved.
"""

import numpy as np
import pytest

from contracts import CapitalConfig
from harness.api import BacktestResult
from harness.engine.runner import run_backtest
from harness.metrics.core import profit_factor
from tests.adversarial.strategies.cost_blind import DEFAULT_LOOKBACK, CostBlindReversal
from tests.adversarial.worlds import cost_blind_world


def _t_stat(result: BacktestResult) -> float:
    r = np.asarray(result.daily_returns)
    return float(r.mean() / r.std() * np.sqrt(len(r)))


def _pf(result: BacktestResult) -> float:
    return profit_factor([t.pnl for t in result.trades])


@pytest.fixture(scope="module")
def runs():
    world = cost_blind_world(2)
    capital = CapitalConfig(effective_at=world.timestamps[0], source="default")
    return {
        m: run_backtest(
            provider=world.provider(),
            strategy=CostBlindReversal(world.symbols),
            symbols=list(world.symbols),
            start=world.timestamps[0],
            end=world.timestamps[-1],
            capital=capital,
            cost_multiplier=m,
        )
        for m in (1.0, 2.0)
    }


def test_profitable_at_1x_costs(runs):
    assert _pf(runs[1.0]) > 1.3
    assert _t_stat(runs[1.0]) > 2.0


def test_unprofitable_at_2x_costs(runs):
    assert _pf(runs[2.0]) < 0.8
    assert _t_stat(runs[2.0]) < -2.0


def test_it_trades_a_lot(runs):
    assert len(runs[1.0].trades) >= 300


def test_param_has_four_distinct_stability_neighbours():
    p = DEFAULT_LOOKBACK
    neighbours = {round(p * f) for f in (0.8, 0.9, 1.1, 1.2)} - {p}
    assert len(neighbours) == 4
