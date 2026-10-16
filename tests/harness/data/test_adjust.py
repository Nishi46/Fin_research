import itertools

import pytest

from contracts.data import CorporateAction, CorporateActionKind
from harness.data.adjust import adjust_bars, find_unadjusted_jumps, find_unrecorded_splits
from tests.data_fixtures import (
    dividend_ex_date,
    dividend_series,
    raw_split_series,
    split_ex_date,
)


def _split() -> CorporateAction:
    return CorporateAction(
        symbol="SPL", kind=CorporateActionKind.SPLIT, effective_at=split_ex_date(), value=2.0
    )


def test_two_for_one_split_is_not_a_fifty_percent_drop() -> None:
    raw = raw_split_series()
    adjusted = adjust_bars(raw, [_split()])
    returns = [b.close / a.close - 1 for a, b in itertools.pairwise(adjusted)]
    assert min(returns) > -0.05
    assert find_unadjusted_jumps(adjusted) == []


def test_raw_series_shows_the_split_as_a_crash_so_the_check_fires() -> None:
    issues = find_unadjusted_jumps(raw_split_series())
    assert len(issues) == 1
    assert issues[0].kind == "suspect_jump"


def test_split_adjustment_scales_pre_split_prices_and_volume_only() -> None:
    raw = raw_split_series()
    adjusted = adjust_bars(raw, [_split()])
    assert adjusted[0].close == pytest.approx(50.0)
    assert adjusted[0].volume == pytest.approx(raw[0].volume * 2)
    assert adjusted[4].close == raw[4].close
    assert adjusted[4].volume == raw[4].volume


def test_adjustment_does_not_mutate_input() -> None:
    raw = raw_split_series()
    before = [b.close for b in raw]
    adjust_bars(raw, [_split()])
    assert [b.close for b in raw] == before


def test_actions_after_end_are_not_applied() -> None:
    raw = raw_split_series()
    adjusted = adjust_bars(raw, [_split()], end=raw[2].ts)
    assert [b.close for b in adjusted] == [b.close for b in raw]


def test_dividend_adjusts_prior_prices_by_proportional_factor() -> None:
    raw = dividend_series()
    action = CorporateAction(
        symbol="DIV", kind=CorporateActionKind.DIVIDEND, effective_at=dividend_ex_date(), value=1.0
    )
    adjusted = adjust_bars(raw, [action])
    factor = 1 - 1.0 / raw[1].close
    assert adjusted[0].close == pytest.approx(raw[0].close * factor)
    assert adjusted[1].close == pytest.approx(raw[1].close * factor)
    assert adjusted[2].close == raw[2].close
    assert adjusted[0].volume == raw[0].volume


def test_unrecorded_split_is_flagged() -> None:
    issues = find_unrecorded_splits(raw_split_series(), [])
    assert [i.kind for i in issues] == ["unrecorded_split"]


def test_recorded_split_is_not_flagged_as_unrecorded() -> None:
    assert find_unrecorded_splits(raw_split_series(), [_split()]) == []
