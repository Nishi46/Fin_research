from datetime import date

from harness.data.crosscheck import StooqCrossChecker, YFinanceCrossChecker
from harness.data.reconcile import ReconcileConfig
from tests.data_fixtures import load_json, load_text, series


def _primary() -> list:  # type: ignore[type-arg]
    return series("AAA", [100.0, 100.5, 101.0], start=date(2026, 1, 5))


def test_stooq_parses_csv_and_reconciles_with_primary() -> None:
    checker = StooqCrossChecker(lambda symbol, start, end: load_text("stooq_aaa.csv"))
    report = checker.check(_primary(), ["AAA"])
    assert report.source == "stooq"
    assert report.compared == 3
    assert report.ok


def test_yfinance_rows_reconcile() -> None:
    rows = load_json("yfinance_aaa.json")["rows"]
    checker = YFinanceCrossChecker(lambda symbol, start, end: rows)
    report = checker.check(_primary(), ["AAA"], ReconcileConfig(min_overlap=0.5))
    assert report.source == "yfinance"
    assert report.compared == 2
    assert report.ok


def test_disagreeing_stooq_fails() -> None:
    bad = (
        load_text("stooq_aaa.csv")
        .replace("100.5,1400000", "108.0,1400000")
        .replace("100.8", "108.1")
    )
    checker = StooqCrossChecker(lambda symbol, start, end: bad)
    report = checker.check(_primary(), ["AAA"], ReconcileConfig(max_disagree_fraction=0.1))
    assert not report.ok


def test_checkers_are_not_data_providers() -> None:
    for checker in (
        StooqCrossChecker(lambda s, a, b: ""),
        YFinanceCrossChecker(lambda s, a, b: []),
    ):
        assert not hasattr(checker, "bars")
        assert not hasattr(checker, "snapshot")
