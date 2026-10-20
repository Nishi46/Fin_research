from datetime import UTC, date, datetime

from contracts.data import CorporateAction, CorporateActionKind
from scripts.reconcile_sources import relative_differences, split_only, summarize
from tests.data_fixtures import series


def test_differences_are_relative_absolute_and_only_for_shared_days() -> None:
    mine = series("AAA", [100.0, 102.0, 50.0], start=date(2026, 1, 5))
    theirs = series("AAA", [100.0, 100.0], start=date(2026, 1, 5))
    diffs = relative_differences(mine, theirs)
    assert [round(d.relative, 6) for d in diffs] == [0.0, 0.02]


def test_only_splits_survive_the_basis_filter() -> None:
    when = datetime(2026, 1, 5, tzinfo=UTC)
    split = CorporateAction(
        symbol="AAA", kind=CorporateActionKind.SPLIT, value=4.0, effective_at=when
    )
    dividend = CorporateAction(
        symbol="AAA", kind=CorporateActionKind.DIVIDEND, value=0.5, effective_at=when
    )
    assert split_only([split, dividend]) == [split]


def test_summary_reports_percentiles_and_worst_bars() -> None:
    mine = series("AAA", [100.0, 101.0, 130.0], start=date(2026, 1, 5))
    theirs = series("AAA", [100.0, 100.0, 100.0], start=date(2026, 1, 5))
    lines = summarize(relative_differences(mine, theirs))
    assert lines[0] == "bars compared: 3"
    assert any("p99:" in line for line in lines)
    assert any("AAA 2026-01-07 0.30000" in line for line in lines)


def test_summary_of_nothing_says_so() -> None:
    assert summarize([]) == ["no overlapping bars to summarize"]


def test_disagreements_are_counted_per_symbol_and_span_reported() -> None:
    from scripts.reconcile_sources import per_symbol_report

    mine = series("AAA", [100.0, 100.0, 200.0], start=date(2026, 1, 5)) + series(
        "BBB", [50.0, 50.0], start=date(2026, 1, 5)
    )
    theirs = series("AAA", [100.0, 100.0, 100.0], start=date(2026, 1, 5)) + series(
        "BBB", [50.0, 50.0], start=date(2026, 1, 5)
    )
    lines = per_symbol_report(mine, relative_differences(mine, theirs), tolerance=0.01)
    assert any(line.startswith("AAA") and "1 beyond" in line and "3 bars" in line for line in lines)
    assert any(line.startswith("BBB") and "0 beyond" in line and "2 bars" in line for line in lines)
    assert any("2026-01-05" in line for line in lines)
