from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from contracts.capital import CapitalConfig
from contracts.costs import MarketState, Order
from harness.costs.factories import COST_MODEL_FACTORIES
from harness.costs.model import (
    LiquidityCapExceededError,
    RealCostModel,
    ScaledCostModel,
)
from tests.helpers import T0

CAPITAL = CapitalConfig(effective_at=T0, source="default")


def market(
    adv: float = 50_000_000.0, price: float = 100.0, spread_bps: float | None = None
) -> MarketState:
    return MarketState(
        symbol="SPY", asof=T0, price=price, avg_daily_dollar_volume=adv, spread_bps=spread_bps
    )


def order(shares: float, side: str = "buy") -> Order:
    return Order(symbol="SPY", side=side, shares=shares)


def test_factory_is_registered_for_the_contract_suite() -> None:
    model = COST_MODEL_FACTORIES["real"]()
    assert isinstance(model, RealCostModel)


def test_known_value_with_all_components() -> None:
    model = RealCostModel(
        default_spread_bps=4.0,
        slippage_bps=1.0,
        commission_per_share=0.01,
        impact_bps_at_full_adv=100.0,
        max_adv_participation=0.5,
    )
    # notional 100_000, participation 0.001 over ADV 100M
    m = market(adv=100_000_000.0, spread_bps=6.0)
    got = model.cost(order(1000), CAPITAL, m)
    half_spread = 100_000 * 3.0 / 10_000
    slippage = 100_000 * 1.0 / 10_000
    commission = 1000 * 0.01
    impact = 100_000 * 100.0 * (0.001**0.5) / 10_000
    assert got == pytest.approx(half_spread + slippage + commission + impact)


def test_quoted_spread_overrides_default() -> None:
    model = RealCostModel(default_spread_bps=2.0)
    assert model.cost(order(10), CAPITAL, market(spread_bps=20.0)) > model.cost(
        order(10), CAPITAL, market()
    )


def test_buy_and_sell_cost_the_same() -> None:
    model = RealCostModel()
    assert model.cost(order(50, "buy"), CAPITAL, market()) == pytest.approx(
        model.cost(order(50, "sell"), CAPITAL, market())
    )


def test_orders_over_the_liquidity_cap_are_refused() -> None:
    model = RealCostModel(max_adv_participation=0.01)
    # cap = 1% of 1M = 10_000 USD; 200 shares * 100 = 20_000
    with pytest.raises(LiquidityCapExceededError, match="liquidity"):
        model.cost(order(200), CAPITAL, market(adv=1_000_000.0))
    assert model.cost(order(99), CAPITAL, market(adv=1_000_000.0)) > 0


def test_max_shares_matches_the_cap() -> None:
    model = RealCostModel(max_adv_participation=0.05)
    m = market(adv=2_000_000.0, price=50.0)
    assert model.max_shares(m) == pytest.approx(2_000.0)
    model.cost(order(model.max_shares(m)), CAPITAL, m)


def test_zero_volume_is_refused_for_max_shares_too() -> None:
    with pytest.raises(ValueError, match="volume"):
        RealCostModel().max_shares(market(adv=0.0))


def test_same_order_costs_more_when_it_is_a_bigger_share_of_volume() -> None:
    model = RealCostModel()
    assert model.cost(order(100), CAPITAL, market(adv=5e6)) > model.cost(
        order(100), CAPITAL, market(adv=5e8)
    )


def test_cost_does_not_depend_on_capital_except_through_order_size() -> None:
    model = RealCostModel()
    big = CapitalConfig(amount=Decimal("5000000"), effective_at=T0, source="website")
    assert model.cost(order(10), big, market()) == model.cost(order(10), CAPITAL, market())


@pytest.mark.parametrize(
    "kwargs",
    [
        {"default_spread_bps": -1.0},
        {"slippage_bps": -0.1},
        {"commission_per_share": -0.01},
        {"impact_bps_at_full_adv": -5.0},
        {"max_adv_participation": 0.0},
        {"max_adv_participation": 1.5},
    ],
)
def test_bad_parameters_are_refused(kwargs: dict[str, float]) -> None:
    with pytest.raises(ValueError, match="must be"):
        RealCostModel(**kwargs)  # type: ignore[arg-type]


@given(
    shares=st.floats(min_value=0.01, max_value=500.0),
    k=st.floats(min_value=1.0, max_value=4.0),
    adv=st.floats(min_value=5e6, max_value=1e10),
)
def test_larger_orders_never_cost_proportionally_less(shares: float, k: float, adv: float) -> None:
    model = RealCostModel(max_adv_participation=1.0)
    small = model.cost(order(shares), CAPITAL, market(adv=adv))
    large = model.cost(order(shares * k), CAPITAL, market(adv=adv))
    assert large >= k * small * (1 - 1e-9)


@given(
    shares=st.floats(min_value=0.01, max_value=500.0), adv=st.floats(min_value=5e6, max_value=1e9)
)
def test_cost_is_positive_and_below_notional(shares: float, adv: float) -> None:
    model = RealCostModel(max_adv_participation=1.0)
    c = model.cost(order(shares), CAPITAL, market(adv=adv))
    assert 0 < c < shares * 100.0


@given(adv=st.floats(min_value=1e6, max_value=1e9), factor=st.floats(min_value=1.01, max_value=50))
def test_less_volume_never_costs_less(adv: float, factor: float) -> None:
    model = RealCostModel(max_adv_participation=1.0)
    assert model.cost(order(100), CAPITAL, market(adv=adv)) >= model.cost(
        order(100), CAPITAL, market(adv=adv * factor)
    )


def test_scaled_model_multiplies_total_cost() -> None:
    base = RealCostModel()
    doubled = ScaledCostModel(base, 2.0)
    assert doubled.cost(order(10), CAPITAL, market()) == pytest.approx(
        2 * base.cost(order(10), CAPITAL, market())
    )


def test_scaled_model_cannot_reduce_costs() -> None:
    with pytest.raises(ValueError, match="at least 1"):
        ScaledCostModel(RealCostModel(), 0.5)


def test_scaled_model_keeps_the_liquidity_refusal() -> None:
    scaled = ScaledCostModel(RealCostModel(max_adv_participation=0.01), 2.0)
    with pytest.raises(LiquidityCapExceededError):
        scaled.cost(order(200), CAPITAL, market(adv=1_000_000.0))


def test_defaults_come_from_the_frozen_thresholds() -> None:
    from harness.thresholds import DEFAULT_THRESHOLDS as T

    model = RealCostModel()
    assert model.default_spread_bps == T.cost_default_spread_bps
    assert model.slippage_bps == T.cost_slippage_bps
    assert model.commission_per_share == T.cost_commission_per_share
    assert model.impact_bps_at_full_adv == T.cost_impact_bps_at_full_adv
    assert model.max_adv_participation == T.max_adv_participation


def test_an_injected_thresholds_object_changes_the_values() -> None:
    from harness.thresholds import Thresholds

    t = Thresholds(
        cost_default_spread_bps=7.0,
        cost_slippage_bps=3.0,
        cost_commission_per_share=0.02,
        cost_impact_bps_at_full_adv=40.0,
        max_adv_participation=0.2,
    )
    model = RealCostModel(thresholds=t)
    assert (model.default_spread_bps, model.slippage_bps) == (7.0, 3.0)
    assert (model.commission_per_share, model.impact_bps_at_full_adv) == (0.02, 40.0)
    assert model.max_adv_participation == 0.2
    assert model.cost(order(10), CAPITAL, market()) != RealCostModel().cost(
        order(10), CAPITAL, market()
    )


def test_explicit_arguments_still_override_thresholds() -> None:
    assert RealCostModel(slippage_bps=9.0).slippage_bps == 9.0
