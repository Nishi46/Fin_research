"""Task 41: performance exists at exactly one parameter value and collapses next to it."""

import pytest

from tests.adversarial._sim import simulate
from tests.adversarial.strategies.param_peak import ParamPeakStrategy
from tests.adversarial.worlds import ASSUMED_COST_BPS_PER_SIDE, PEAK_PERIOD, param_peak_world


@pytest.fixture(scope="module")
def sweep():
    world = param_peak_world(3)
    return {
        p: simulate(
            world, ParamPeakStrategy(world.symbols, period=p), cost_bps=ASSUMED_COST_BPS_PER_SIDE
        )
        for p in range(PEAK_PERIOD - 2, PEAK_PERIOD + 3)
    }


def test_peak_is_significant(sweep):
    peak = sweep[PEAK_PERIOD]
    assert peak.net_mean > 0
    assert peak.t_stat(peak.net) > 3
    assert peak.profit_factor > 1.1


def test_neighbours_collapse_far_below_the_20_percent_tolerance(sweep):
    peak = sweep[PEAK_PERIOD].net_mean
    for p in (PEAK_PERIOD - 2, PEAK_PERIOD - 1, PEAK_PERIOD + 1, PEAK_PERIOD + 2):
        assert sweep[p].net_mean < 0.2 * peak


def test_strategy_reports_its_parameter():
    assert ParamPeakStrategy(("A",), period=PEAK_PERIOD).params == {"period": PEAK_PERIOD}
