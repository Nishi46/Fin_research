from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from contracts.costs import MarketState, Order
from contracts.splitting import Split

D0 = datetime(2026, 1, 1, tzinfo=UTC)


def d(n: int) -> datetime:
    return D0 + timedelta(days=n)


def test_valid_split() -> None:
    s = Split(train_start=d(0), train_end=d(10), test_start=d(12), test_end=d(20))
    assert s.test_start > s.train_end


def test_train_must_end_before_test_starts() -> None:
    with pytest.raises(ValidationError):
        Split(train_start=d(0), train_end=d(10), test_start=d(10), test_end=d(20))


def test_test_window_must_not_be_inverted() -> None:
    with pytest.raises(ValidationError):
        Split(train_start=d(0), train_end=d(10), test_start=d(15), test_end=d(14))


def test_train_window_must_not_be_inverted() -> None:
    with pytest.raises(ValidationError):
        Split(train_start=d(5), train_end=d(4), test_start=d(10), test_end=d(12))


def test_rejects_naive_datetimes() -> None:
    with pytest.raises(ValidationError):
        Split(
            train_start=datetime(2026, 1, 1),
            train_end=d(10),
            test_start=d(12),
            test_end=d(20),
        )


def test_order_rejects_non_positive_shares() -> None:
    with pytest.raises(ValidationError):
        Order(symbol="SPY", side="buy", shares=0)


def test_market_state_rejects_non_positive_price() -> None:
    with pytest.raises(ValidationError):
        MarketState(symbol="SPY", asof=d(0), price=0.0, avg_daily_dollar_volume=1e9)
