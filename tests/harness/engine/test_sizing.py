from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from contracts import CapitalConfig, Intent, MarketState
from harness.engine.sizing import DefaultSizer
from tests.helpers import T0


def cap(amount: str) -> CapitalConfig:
    return CapitalConfig(amount=Decimal(amount), effective_at=T0, source="website")


def mkt(symbol: str, price: float, adv: float = 1e9) -> MarketState:
    return MarketState(symbol=symbol, asof=T0, price=price, avg_daily_dollar_volume=adv)


def intent(**weights: float) -> Intent:
    return Intent(asof=T0, weights=weights)


def test_weights_become_whole_shares_rounded_down() -> None:
    sizer = DefaultSizer()
    out = sizer.target_shares(intent(AAA=0.5), cap("10000"), {"AAA": mkt("AAA", 30.0)})
    assert out == {"AAA": 166.0}  # 5000 / 30 = 166.67


def test_fractional_mode_keeps_the_fraction() -> None:
    sizer = DefaultSizer(fractional=True)
    out = sizer.target_shares(intent(AAA=0.5), cap("10000"), {"AAA": mkt("AAA", 30.0)})
    assert out["AAA"] == pytest.approx(5000 / 30, abs=1e-5)


def test_same_intent_at_different_capital_gives_different_positions() -> None:
    sizer = DefaultSizer()
    weights = intent(AAA=0.05, BBB=0.05, CCC=0.9)
    market = {"AAA": mkt("AAA", 400.0), "BBB": mkt("BBB", 150.0), "CCC": mkt("CCC", 20.0)}
    small = sizer.target_shares(weights, cap("2000"), market)
    large = sizer.target_shares(weights, cap("200000"), market)
    assert [s for s, n in small.items() if n > 0] == ["CCC"]  # 100 USD cannot buy a 400 share
    assert len([s for s, n in large.items() if n > 0]) == 3
    assert large["CCC"] > small["CCC"] * 50


def test_liquidity_cap_limits_position_to_share_of_adv() -> None:
    sizer = DefaultSizer(max_adv_participation=0.01)
    out = sizer.target_shares(
        intent(THIN=1.0), cap("1000000"), {"THIN": mkt("THIN", 10.0, adv=2_000_000.0)}
    )
    assert out["THIN"] == 2_000.0  # 1% of 2M = 20_000 USD = 2000 shares, not 100_000


def test_position_below_minimum_notional_is_dropped() -> None:
    sizer = DefaultSizer(min_position_usd=50.0, fractional=True)
    out = sizer.target_shares(
        intent(AAA=0.004, BBB=0.5), cap("10000"), {"AAA": mkt("AAA", 10.0), "BBB": mkt("BBB", 10.0)}
    )
    assert out["AAA"] == 0.0
    assert out["BBB"] > 0


def test_empty_intent_is_cash() -> None:
    assert DefaultSizer().target_shares(intent(), cap("10000"), {}) == {}


def test_missing_market_state_is_an_error_not_a_zero() -> None:
    with pytest.raises(ValueError, match="no market state"):
        DefaultSizer().target_shares(intent(AAA=0.5), cap("10000"), {})


def test_zero_volume_symbol_gets_no_position() -> None:
    out = DefaultSizer().target_shares(intent(AAA=0.5), cap("10000"), {"AAA": mkt("AAA", 10, 0.0)})
    assert out["AAA"] == 0.0


def test_bad_parameters_refused() -> None:
    with pytest.raises(ValueError, match="max_adv_participation"):
        DefaultSizer(max_adv_participation=0.0)
    with pytest.raises(ValueError, match="min_position_usd"):
        DefaultSizer(min_position_usd=-1.0)


@given(
    amount=st.integers(min_value=100, max_value=10_000_000),
    weight=st.floats(min_value=0.0, max_value=1.0),
    price=st.floats(min_value=1.0, max_value=2000.0),
)
def test_never_allocates_more_than_the_weight(amount: int, weight: float, price: float) -> None:
    out = DefaultSizer().target_shares(
        intent(AAA=weight), cap(str(amount)), {"AAA": mkt("AAA", price)}
    )
    assert out["AAA"] * price <= weight * amount + 1e-6


def test_sizer_and_backtest_config_default_to_the_frozen_cap() -> None:
    from harness.engine.backtest import BacktestConfig
    from harness.thresholds import DEFAULT_THRESHOLDS

    cap_value = DEFAULT_THRESHOLDS.max_adv_participation
    assert DefaultSizer().max_adv_participation == cap_value
    assert BacktestConfig().max_adv_participation == cap_value
