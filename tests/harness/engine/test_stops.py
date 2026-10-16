from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from harness.engine.stops import StopRule, crosses_event, long_exit_fill

RULE = StopRule(stop_loss_frac=0.05, take_profit_frac=0.10)
ENTRY = 100.0  # stop at 95, target at 110


def test_no_trigger_means_no_exit() -> None:
    assert long_exit_fill(ENTRY, RULE, open_=100, high=105, low=97) is None


def test_intrabar_stop_fills_at_the_stop_price() -> None:
    fill = long_exit_fill(ENTRY, RULE, open_=99, high=100, low=94)
    assert fill is not None
    assert (fill.price, fill.reason) == (95.0, "stop")


def test_gap_through_stop_fills_at_the_open_not_the_stop() -> None:
    fill = long_exit_fill(ENTRY, RULE, open_=88, high=90, low=85)
    assert fill is not None
    assert fill.price == 88.0
    assert fill.price != 95.0
    assert fill.reason == "gap_stop"


def test_open_exactly_at_the_stop_counts_as_a_gap_fill() -> None:
    fill = long_exit_fill(ENTRY, RULE, open_=95, high=96, low=93)
    assert fill is not None
    assert fill.reason == "gap_stop"
    assert fill.price == 95.0


def test_gap_up_through_target_fills_at_the_open() -> None:
    fill = long_exit_fill(ENTRY, RULE, open_=115, high=120, low=114)
    assert fill is not None
    assert (fill.price, fill.reason) == (115.0, "gap_take_profit")


def test_intrabar_target_fills_at_the_target() -> None:
    fill = long_exit_fill(ENTRY, RULE, open_=105, high=112, low=104)
    assert fill is not None
    assert fill.price == pytest.approx(110.0)
    assert fill.reason == "take_profit"


def test_stop_and_target_in_one_bar_assumes_the_stop_first() -> None:
    fill = long_exit_fill(ENTRY, RULE, open_=100, high=115, low=90)
    assert fill is not None
    assert fill.reason == "stop"


def test_stop_only_rule_never_takes_profit() -> None:
    rule = StopRule(stop_loss_frac=0.05)
    assert long_exit_fill(ENTRY, rule, open_=100, high=500, low=99) is None


def test_target_only_rule_never_stops_out() -> None:
    rule = StopRule(take_profit_frac=0.10)
    assert long_exit_fill(ENTRY, rule, open_=100, high=101, low=1) is None


def test_rule_needs_valid_fractions() -> None:
    with pytest.raises(ValidationError):
        StopRule(stop_loss_frac=1.0)
    with pytest.raises(ValidationError):
        StopRule(stop_loss_frac=0.0)
    with pytest.raises(ValidationError):
        StopRule(take_profit_frac=-0.1)


T = datetime(2024, 3, 1, 21, tzinfo=UTC)


def test_hold_across_an_event_is_flagged() -> None:
    event = T + timedelta(days=2)
    assert crosses_event(T, T + timedelta(days=4), [event])


def test_hold_that_ends_before_the_event_is_not_flagged() -> None:
    assert not crosses_event(T, T + timedelta(days=1), [T + timedelta(days=2)])


def test_event_before_entry_is_not_flagged() -> None:
    assert not crosses_event(T, T + timedelta(days=4), [T - timedelta(days=1)])


def test_exit_on_the_event_bar_counts_as_crossing() -> None:
    event = T + timedelta(days=2)
    assert crosses_event(T, event, [event])
    assert not crosses_event(event, event + timedelta(days=1), [event])
