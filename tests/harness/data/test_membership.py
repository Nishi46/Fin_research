from datetime import date

from harness.data.membership import IndexHistory, PointInTimeMembership, parse_changes_csv
from tests.data_fixtures import load_text


def _history() -> IndexHistory:
    changes = parse_changes_csv(load_text("membership_changes.csv"))
    return IndexHistory(current=frozenset({"AAA", "BBB", "NEWCO"}), changes=changes)


def test_parse_skips_comments_and_splits_tickers() -> None:
    changes = parse_changes_csv(load_text("membership_changes.csv"))
    assert len(changes) == 3
    assert changes[1].added == frozenset({"BBB", "CCC"})
    assert changes[1].removed == frozenset()


def test_today_equals_current() -> None:
    assert _history().members_on(date(2026, 6, 1)) == frozenset({"AAA", "BBB", "NEWCO"})


def test_replays_changes_backwards() -> None:
    h = _history()
    assert h.members_on(date(2026, 2, 15)) == frozenset({"AAA", "BBB", "CCC", "NEWCO"})
    assert h.members_on(date(2026, 2, 1)) == frozenset({"AAA", "NEWCO"})
    assert h.members_on(date(2026, 1, 10)) == frozenset({"AAA", "OLDCO"})


def test_change_date_is_inclusive() -> None:
    assert "NEWCO" in _history().members_on(date(2026, 1, 20))
    assert "NEWCO" not in _history().members_on(date(2026, 1, 19))


def test_provider_wrapper_returns_frozenset_and_empty_for_unknown() -> None:
    pit = PointInTimeMembership({"SP500": _history()})
    assert isinstance(pit.membership("SP500", date(2026, 6, 1)), frozenset)
    assert pit.membership("NOPE", date(2026, 6, 1)) == frozenset()
