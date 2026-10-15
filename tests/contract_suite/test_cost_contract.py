import pytest

from contracts.capital import CapitalConfig
from contracts.costs import CostModel, MarketState, Order
from tests.helpers import T0

CAPITAL = CapitalConfig(effective_at=T0, source="default")


def market(adv: float = 50_000_000.0) -> MarketState:
    return MarketState(symbol="SPY", asof=T0, price=500.0, avg_daily_dollar_volume=adv)


def order(shares: float) -> Order:
    return Order(symbol="SPY", side="buy", shares=shares)


def test_cost_is_positive(cost_model: CostModel) -> None:
    assert cost_model.cost(order(10), CAPITAL, market()) > 0


def test_larger_orders_never_cost_proportionally_less(cost_model: CostModel) -> None:
    small = cost_model.cost(order(10), CAPITAL, market())
    large = cost_model.cost(order(20), CAPITAL, market())
    assert large >= 2 * small


def test_thinner_markets_cost_more(cost_model: CostModel) -> None:
    liquid = cost_model.cost(order(100), CAPITAL, market(adv=500_000_000.0))
    thin = cost_model.cost(order(100), CAPITAL, market(adv=5_000_000.0))
    assert thin > liquid


def test_zero_volume_market_is_refused(cost_model: CostModel) -> None:
    with pytest.raises(ValueError, match="volume"):
        cost_model.cost(order(10), CAPITAL, market(adv=0.0))
