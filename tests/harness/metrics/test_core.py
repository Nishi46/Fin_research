import math

import pytest
from hypothesis import given
from hypothesis import strategies as st

from harness.metrics.core import (
    annualized_return,
    calmar,
    equity_curve,
    max_drawdown,
    profit_factor,
    sharpe,
)


def test_profit_factor_known_value() -> None:
    assert profit_factor([10.0, -5.0, 20.0, -5.0]) == pytest.approx(3.0)


def test_profit_factor_all_losses_is_zero() -> None:
    assert profit_factor([-1.0, -2.0]) == 0.0


def test_profit_factor_no_losses_is_infinite() -> None:
    assert profit_factor([1.0, 2.0]) == math.inf


def test_profit_factor_no_trades_is_zero_not_a_pass() -> None:
    assert profit_factor([]) == 0.0
    assert profit_factor([0.0, 0.0]) == 0.0


def test_sharpe_known_value() -> None:
    returns = [0.01, -0.01, 0.02, 0.0]
    mean = 0.005
    var = sum((r - mean) ** 2 for r in returns) / 3
    assert sharpe(returns) == pytest.approx(mean / math.sqrt(var) * math.sqrt(252))


def test_sharpe_flat_series_is_zero() -> None:
    assert sharpe([0.0] * 50) == 0.0
    assert sharpe([0.01] * 50) == 0.0  # no variance: undefined, reported as 0 not infinity


def test_sharpe_too_short_is_zero() -> None:
    assert sharpe([0.01]) == 0.0
    assert sharpe([]) == 0.0


def test_sharpe_losing_series_negative() -> None:
    assert sharpe([-0.01, -0.02, 0.0, -0.015]) < 0


def test_equity_curve_compounds() -> None:
    assert equity_curve([0.1, -0.1]) == pytest.approx([1.1, 0.99])


def test_max_drawdown_known_value() -> None:
    # 1.0 -> 1.2 -> 0.6 -> 0.9: peak 1.2, trough 0.6 => 50%
    returns = [0.2, -0.5, 0.5]
    assert max_drawdown(returns) == pytest.approx(0.5)


def test_max_drawdown_flat_and_rising_is_zero() -> None:
    assert max_drawdown([0.0, 0.0]) == 0.0
    assert max_drawdown([0.01, 0.02]) == 0.0
    assert max_drawdown([]) == 0.0


def test_max_drawdown_counts_loss_from_starting_capital() -> None:
    assert max_drawdown([-0.1, 0.0]) == pytest.approx(0.1)


def test_annualized_return_one_year_of_252_days() -> None:
    daily = 1.1 ** (1 / 252) - 1
    assert annualized_return([daily] * 252) == pytest.approx(0.1)


def test_calmar_known_value() -> None:
    returns = [0.2, -0.5, 0.5]
    ann = annualized_return(returns)
    assert calmar(returns) == pytest.approx(ann / 0.5)


def test_calmar_no_drawdown_positive_return_is_infinite_flat_is_zero() -> None:
    assert calmar([0.01, 0.01]) == math.inf
    assert calmar([0.0, 0.0]) == 0.0


def test_calmar_losing_series_negative() -> None:
    assert calmar([-0.01, -0.02, 0.005]) < 0


def test_total_loss_does_not_crash() -> None:
    assert annualized_return([-1.0, 0.5]) == -1.0
    assert max_drawdown([-1.0, 0.5]) == 1.0


@given(st.lists(st.floats(min_value=-0.5, max_value=0.5), min_size=1, max_size=200))
def test_drawdown_is_a_fraction(returns: list[float]) -> None:
    assert 0.0 <= max_drawdown(returns) <= 1.0


@given(st.lists(st.floats(min_value=-0.5, max_value=0.5), min_size=2, max_size=200))
def test_sharpe_flips_sign_with_returns(returns: list[float]) -> None:
    s = sharpe(returns)
    assert math.isfinite(s)
    assert sharpe([-r for r in returns]) == pytest.approx(-s, abs=1e-6)


def test_rejects_non_finite() -> None:
    with pytest.raises(ValueError, match="finite"):
        sharpe([0.1, math.nan])
    with pytest.raises(ValueError, match="finite"):
        max_drawdown([math.inf])
