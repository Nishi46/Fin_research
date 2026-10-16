from harness.data.quality import find_gaps, find_stale_bars
from tests.data_fixtures import gapped_series, normal_series, stale_series


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
