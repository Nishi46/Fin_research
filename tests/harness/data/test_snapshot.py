from datetime import date

from contracts.data import CorporateAction, CorporateActionKind
from harness.data.snapshot import build_snapshot
from tests.data_fixtures import dividend_ex_date, normal_series


def test_same_content_same_id_and_always_survivorship_biased() -> None:
    a = build_snapshot(normal_series())
    b = build_snapshot(normal_series())
    assert a.id == b.id
    assert a.survivorship_biased is True
    assert a.id.startswith("sha256:")


def test_id_is_order_independent() -> None:
    bars = normal_series("AAA") + normal_series("BBB")
    assert build_snapshot(bars).id == build_snapshot(list(reversed(bars))).id


def test_changed_bar_changes_id() -> None:
    bars = normal_series()
    changed = [bars[0].model_copy(update={"volume": bars[0].volume + 1}), *bars[1:]]
    assert build_snapshot(bars).id != build_snapshot(changed).id


def test_actions_and_membership_are_part_of_the_hash() -> None:
    bars = normal_series()
    action = CorporateAction(
        symbol="AAA", kind=CorporateActionKind.DIVIDEND, effective_at=dividend_ex_date(), value=1.0
    )
    base = build_snapshot(bars).id
    assert build_snapshot(bars, actions=[action]).id != base
    members = {("SP500", date(2026, 1, 5)): frozenset({"AAA"})}
    assert build_snapshot(bars, memberships=members).id != base


def test_range_matches_bars() -> None:
    bars = normal_series()
    snap = build_snapshot(bars)
    assert snap.start == bars[0].ts
    assert snap.end == bars[-1].ts
