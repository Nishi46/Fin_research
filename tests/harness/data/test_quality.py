from datetime import date

from harness.data.common import Issue, SpinOff
from harness.data.quality import find_gaps, find_spin_offs, find_stale_bars
from tests.data_fixtures import gapped_series, normal_series, series, stale_series


def test_clean_series_has_no_gaps_or_stale_bars() -> None:
    bars = normal_series()
    assert find_gaps(bars) == []
    assert find_stale_bars(bars) == []


def test_missing_sessions_are_reported_as_gaps() -> None:
    issues = find_gaps(gapped_series())
    assert len(issues) == 2
    assert {i.kind for i in issues} == {"gap"}


def test_holidays_are_not_gaps() -> None:
    # 20 sessions starting 2026-01-05 span the MLK holiday on 2026-01-19.
    assert find_gaps(normal_series(n=20)) == []


def test_frozen_zero_volume_bars_are_stale() -> None:
    issues = find_stale_bars(stale_series())
    assert len(issues) == 3
    assert {i.kind for i in issues} == {"stale"}


def test_gaps_are_per_symbol() -> None:
    mixed = normal_series("AAA") + gapped_series("GAP")
    assert {i.symbol for i in find_gaps(mixed)} == {"GAP"}


# normal_series(n=10) covers sessions 2026-01-05 .. 2026-01-16.
FIRST_DAY = date(2026, 1, 5)
LAST_DAY = date(2026, 1, 16)


def _spin(
    symbol: str = "AAA", ex: date = date(2026, 1, 9), new: str = "NEW", rate: float = 0.5
) -> SpinOff:
    return SpinOff(source_symbol=symbol, ex_date=ex, new_symbol=new, new_rate=rate)


def test_spin_off_inside_range_is_flagged() -> None:
    issues = find_spin_offs(normal_series("AAA", n=10), [_spin()])
    assert issues == [
        Issue(
            "spin_off",
            "AAA",
            None,
            "spin-off of NEW (rate 0.5) on 2026-01-09: raw prices are not adjusted for it",
        )
    ]


def test_spin_off_before_first_bar_is_ignored() -> None:
    assert find_spin_offs(normal_series("AAA", n=10), [_spin(ex=date(2026, 1, 2))]) == []


def test_spin_off_after_last_bar_is_ignored() -> None:
    assert find_spin_offs(normal_series("AAA", n=10), [_spin(ex=date(2026, 1, 20))]) == []


def test_spin_off_for_unknown_symbol_is_ignored() -> None:
    assert find_spin_offs(normal_series("AAA", n=10), [_spin(symbol="ZZZ")]) == []


def test_spin_off_on_first_and_last_session_is_flagged() -> None:
    issues = find_spin_offs(normal_series("AAA", n=10), [_spin(ex=FIRST_DAY), _spin(ex=LAST_DAY)])
    assert len(issues) == 2


def test_spin_off_on_weekend_inside_range_is_flagged() -> None:
    issues = find_spin_offs(normal_series("AAA", n=10), [_spin(ex=date(2026, 1, 10))])
    assert [i.kind for i in issues] == ["spin_off"]


def test_spin_offs_across_multiple_symbols() -> None:
    bars = normal_series("AAA", n=10) + series("BBB", [50.0 + i for i in range(10)])
    issues = find_spin_offs(bars, [_spin("AAA"), _spin("BBB", new="OTHER"), _spin("CCC")])
    assert [i.symbol for i in issues] == ["AAA", "BBB"]


def test_spin_offs_are_ordered_by_ex_date_then_symbol() -> None:
    bars = normal_series("AAA", n=10) + normal_series("BBB", n=10)
    spins = [
        _spin("BBB", ex=date(2026, 1, 12)),
        _spin("BBB", ex=date(2026, 1, 8)),
        _spin("AAA", ex=date(2026, 1, 12)),
    ]
    issues = find_spin_offs(bars, spins)
    assert [(i.symbol, i.detail.split(" on ")[1][:10]) for i in issues] == [
        ("BBB", "2026-01-08"),
        ("AAA", "2026-01-12"),
        ("BBB", "2026-01-12"),
    ]


def test_duplicate_spin_offs_produce_one_issue_each() -> None:
    assert len(find_spin_offs(normal_series("AAA", n=10), [_spin(), _spin()])) == 2


def test_no_spin_offs_returns_empty() -> None:
    assert find_spin_offs(normal_series("AAA", n=10), []) == []
