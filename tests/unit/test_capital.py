from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from pydantic import ValidationError

from contracts.capital import CapitalConfig

NOW = datetime(2026, 1, 5, 14, 0, tzinfo=UTC)


def test_default_amount_is_ten_thousand_usd() -> None:
    cfg = CapitalConfig(effective_at=NOW, source="default")
    assert cfg.amount == Decimal("10000")
    assert cfg.currency == "USD"


def test_explicit_amount_is_kept() -> None:
    cfg = CapitalConfig(amount=Decimal("250000"), effective_at=NOW, source="website")
    assert cfg.amount == Decimal("250000")


@pytest.mark.parametrize("bad", [Decimal(0), Decimal(-1), Decimal("NaN"), Decimal("Infinity")])
def test_rejects_non_positive_or_non_finite_amount(bad: Decimal) -> None:
    with pytest.raises(ValidationError):
        CapitalConfig(amount=bad, effective_at=NOW, source="default")


def test_rejects_naive_datetime() -> None:
    with pytest.raises(ValidationError):
        CapitalConfig(effective_at=datetime(2026, 1, 5, 14, 0), source="default")


def test_normalizes_offset_datetime_to_utc() -> None:
    est = timezone(timedelta(hours=-5))
    cfg = CapitalConfig(effective_at=datetime(2026, 1, 5, 9, 0, tzinfo=est), source="env")
    assert cfg.effective_at.utcoffset() == timedelta(0)
    assert cfg.effective_at.hour == 14


def test_is_immutable() -> None:
    cfg = CapitalConfig(effective_at=NOW, source="default")
    with pytest.raises(ValidationError):
        cfg.amount = Decimal(5)  # type: ignore[misc]


def test_rejects_unknown_source() -> None:
    with pytest.raises(ValidationError):
        CapitalConfig(effective_at=NOW, source="somewhere")
