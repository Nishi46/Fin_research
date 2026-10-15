import itertools
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from harness.api import Regime
from harness.thresholds import DEFAULT_THRESHOLDS, Thresholds


def test_defaults_match_spec_r5() -> None:
    t = DEFAULT_THRESHOLDS
    assert t.embargo_min_bars == 1
    assert t.dsr_min_probability == 0.95
    assert t.pbo_max == 0.5
    assert t.cost_multiplier == 2.0
    assert t.cost_min_profit_factor == 1.0
    assert t.stability_tolerance == 0.20
    assert t.min_trades == 100
    assert t.min_profitable_regimes == 2


def test_thresholds_are_immutable() -> None:
    with pytest.raises(ValidationError):
        DEFAULT_THRESHOLDS.min_trades = 1  # type: ignore[misc]


def test_regimes_are_a_tuple_so_the_table_cannot_be_edited_in_place() -> None:
    assert isinstance(DEFAULT_THRESHOLDS.regimes, tuple)


def test_regime_table_is_ordered_and_does_not_overlap() -> None:
    regimes = DEFAULT_THRESHOLDS.regimes
    assert len(regimes) >= 2
    for earlier, later in itertools.pairwise(regimes):
        assert earlier.end <= later.start


def test_regime_names_are_unique() -> None:
    names = [r.name for r in DEFAULT_THRESHOLDS.regimes]
    assert len(names) == len(set(names))


def test_regime_table_contains_a_crash_and_a_bear_and_a_bull() -> None:
    names = {r.name for r in DEFAULT_THRESHOLDS.regimes}
    assert {"covid_crash", "bear_2022"} <= names
    assert any("bull" in n for n in names)


def test_regime_lookup_is_half_open() -> None:
    crash = next(r for r in DEFAULT_THRESHOLDS.regimes if r.name == "covid_crash")
    assert crash.contains(crash.start)
    assert not crash.contains(crash.end)


def test_overlapping_regimes_rejected() -> None:
    a = Regime(
        name="a", start=datetime(2020, 1, 1, tzinfo=UTC), end=datetime(2020, 6, 1, tzinfo=UTC)
    )
    b = Regime(
        name="b", start=datetime(2020, 5, 1, tzinfo=UTC), end=datetime(2020, 9, 1, tzinfo=UTC)
    )
    with pytest.raises(ValidationError):
        Thresholds(regimes=(a, b), min_profitable_regimes=1)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("dsr_min_probability", 1.5),
        ("dsr_min_probability", 0.0),
        ("pbo_max", 1.5),
        ("cost_multiplier", 1.0),
        ("stability_tolerance", 0.0),
        ("min_trades", 0),
        ("min_profitable_regimes", 0),
        ("embargo_min_bars", 0),
    ],
)
def test_nonsensical_thresholds_rejected(field: str, value: float) -> None:
    with pytest.raises(ValidationError):
        Thresholds(**{field: value})


def test_cannot_require_more_profitable_regimes_than_exist() -> None:
    with pytest.raises(ValidationError):
        Thresholds(min_profitable_regimes=len(DEFAULT_THRESHOLDS.regimes) + 1)
