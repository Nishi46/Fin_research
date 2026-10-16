import itertools
from datetime import UTC, date, datetime, timedelta

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from contracts.data import Bar
from harness.api import Regime
from harness.data.regimes import coverage_report, derive_regimes
from tests.data_fixtures import series, sp500_anchor_series


def _bar(y: int, m: int, d: int) -> Bar:
    ts = datetime(y, m, d, 21, tzinfo=UTC)
    return Bar(symbol="A", ts=ts, open=1, high=1, low=1, close=1, volume=1)


def _utc(y: int, m: int, d: int) -> datetime:
    return datetime(y, m, d, tzinfo=UTC)


TABLE = (
    Regime(name="low_vol_bull", start=_utc(2016, 1, 1), end=_utc(2018, 1, 26)),
    Regime(name="post_covid_bull", start=_utc(2020, 3, 23), end=_utc(2022, 1, 3)),
    Regime(name="bear_2022", start=_utc(2022, 1, 3), end=_utc(2022, 10, 12)),
)


def test_reports_covered_and_missing_regimes() -> None:
    bars = [_bar(2020, 4, 1), _bar(2020, 4, 2), _bar(2022, 3, 1)]
    report = coverage_report(bars, TABLE, min_sessions=1)
    assert report.covered == ("post_covid_bull", "bear_2022")
    assert "low_vol_bull" in report.missing
    assert report.sessions["post_covid_bull"] == 2


def test_min_sessions_filters_thin_coverage() -> None:
    regimes = [Regime(name="r", start=_utc(2020, 1, 1), end=_utc(2021, 1, 1))]
    bars = [_bar(2020, 6, 1)]
    assert coverage_report(bars, regimes, min_sessions=2).missing == ("r",)
    assert coverage_report(bars, regimes, min_sessions=1).covered == ("r",)


def test_range_outside_table_is_reported() -> None:
    report = coverage_report([_bar(2026, 1, 5)], TABLE, min_sessions=1)
    assert report.uncovered_after_table == 1
    assert report.covered == ()


# --- derive_regimes -------------------------------------------------------------------------


def _shape(regimes: tuple[Regime, ...]) -> list[tuple[str, date, date]]:
    return [(r.name, r.start.date(), r.end.date()) for r in regimes]


def test_regression_anchor_published_sp500_peaks_and_troughs() -> None:
    """The closes at the anchor dates are real published S&P 500 values; the path between
    them is synthetic. The derived boundaries must be exactly the owner-confirmed ones."""
    bars = sp500_anchor_series()
    regimes = derive_regimes(bars, min_drawdown=0.15)
    last_plus_one = bars[-1].ts.date() + timedelta(days=1)
    assert _shape(regimes) == [
        ("selloff_2018-09-20", date(2018, 9, 20), date(2018, 12, 24)),
        ("advance_2018-12-24", date(2018, 12, 24), date(2020, 2, 19)),
        ("selloff_2020-02-19", date(2020, 2, 19), date(2020, 3, 23)),
        ("advance_2020-03-23", date(2020, 3, 23), date(2022, 1, 3)),
        ("selloff_2022-01-03", date(2022, 1, 3), date(2022, 10, 12)),
        ("advance_2022-10-12", date(2022, 10, 12), date(2025, 2, 19)),
        ("selloff_2025-02-19", date(2025, 2, 19), date(2025, 4, 8)),
        ("advance_2025-04-08", date(2025, 4, 8), last_plus_one),
    ]


def test_open_selloff_is_provisional_then_finalizes_with_true_trough() -> None:
    closes = [100.0, 110.0, 100.0, 90.0, 85.0, 88.0]
    open_regimes = derive_regimes(series("X", closes), min_drawdown=0.15)
    bars = series("X", [*closes, 95.0, 120.0])
    assert [r.name for r in open_regimes] == [
        "advance_" + bars[0].ts.strftime("%Y-%m-%d"),
        "selloff_" + bars[1].ts.strftime("%Y-%m-%d") + "_open",
    ]
    assert open_regimes[-1].end.date() == bars[5].ts.date() + timedelta(days=1)
    full = derive_regimes(bars, min_drawdown=0.15)
    assert full[1].name == "selloff_" + bars[1].ts.strftime("%Y-%m-%d")
    assert full[1].end.date() == bars[4].ts.date()  # trough 85
    assert full[2].start.date() == bars[4].ts.date()
    assert full[-1].end.date() == bars[-1].ts.date() + timedelta(days=1)


def test_shallow_dip_is_not_a_selloff_and_exact_threshold_is() -> None:
    shallow = derive_regimes(series("X", [100.0, 88.0, 101.0, 102.0]), min_drawdown=0.15)
    assert [r.name.split("_")[0] for r in shallow] == ["advance"]
    exact = derive_regimes(series("X", [100.0, 85.0, 101.0, 102.0]), min_drawdown=0.15)
    assert [r.name.split("_")[0] for r in exact] == ["selloff", "advance"]


def test_equal_close_does_not_reset_the_record() -> None:
    # record 100, retouch 100 (no new record), then 85 is -15% from the original record.
    regimes = derive_regimes(series("X", [100.0, 100.0, 85.0, 101.0]), min_drawdown=0.15)
    bars = series("X", [100.0, 100.0, 85.0, 101.0])
    assert regimes[0].name == "selloff_" + bars[0].ts.strftime("%Y-%m-%d")


def test_series_starting_with_immediate_fall_has_no_empty_advance() -> None:
    bars = series("X", [100.0, 80.0, 90.0, 101.0])
    regimes = derive_regimes(bars, min_drawdown=0.15)
    assert regimes[0].name.startswith("selloff_")
    assert regimes[0].start.date() == bars[0].ts.date()
    assert all(r.end > r.start for r in regimes)


def test_no_selloffs_is_one_advance() -> None:
    bars = series("X", [100.0, 101.0, 99.0, 102.0])
    regimes = derive_regimes(bars, min_drawdown=0.15)
    assert len(regimes) == 1
    assert regimes[0].start.date() == bars[0].ts.date()


def test_trough_ties_resolve_to_earliest() -> None:
    bars = series("X", [100.0, 80.0, 80.0, 90.0, 120.0])
    regimes = derive_regimes(bars, min_drawdown=0.15)
    assert regimes[0].end.date() == bars[1].ts.date()


def test_boundaries_are_midnight_utc() -> None:
    for r in derive_regimes(sp500_anchor_series(), min_drawdown=0.15):
        assert r.start.time() == datetime.min.time()
        assert r.end.time() == datetime.min.time()


def test_error_cases() -> None:
    good = series("X", [100.0, 90.0, 95.0])
    with pytest.raises(ValueError, match="empty"):
        derive_regimes([], min_drawdown=0.15)
    with pytest.raises(ValueError, match="at least 2"):
        derive_regimes(good[:1], min_drawdown=0.15)
    with pytest.raises(ValueError, match="one symbol"):
        derive_regimes([*good[:2], *series("Y", [1.0, 2.0])], min_drawdown=0.15)
    with pytest.raises(ValueError, match="same date"):
        derive_regimes(
            [good[0], good[0].model_copy(update={"close": 100.0}), good[1]], min_drawdown=0.15
        )
    for bad in (0.0, 1.0, -0.1, 1.5):
        with pytest.raises(ValueError, match="min_drawdown"):
            derive_regimes(good, min_drawdown=bad)


_walks = st.lists(st.floats(min_value=0.85, max_value=1.15), min_size=2, max_size=60)


def _closes(steps: list[float]) -> list[float]:
    out = [100.0]
    for s in steps[1:]:
        out.append(out[-1] * s)
    return out


@settings(deadline=None, max_examples=150)
@given(_walks, st.floats(min_value=0.05, max_value=0.5))
def test_property_partition_and_coverage(steps: list[float], dd: float) -> None:
    bars = series("X", _closes(steps))
    regimes = derive_regimes(bars, min_drawdown=dd)
    assert regimes[0].start.date() == bars[0].ts.date()
    assert regimes[-1].end.date() == bars[-1].ts.date() + timedelta(days=1)
    for a, b in itertools.pairwise(regimes):
        assert a.end == b.start
    for bar in bars:
        assert sum(1 for r in regimes if r.contains(bar.ts)) == 1


@settings(deadline=None, max_examples=150)
@given(_walks, st.floats(min_value=0.05, max_value=0.5), st.data())
def test_property_prefix_stability(steps: list[float], dd: float, data: st.DataObject) -> None:
    bars = series("X", _closes(steps))
    cut = data.draw(st.integers(min_value=2, max_value=len(bars)))
    prefix = derive_regimes(bars[:cut], min_drawdown=dd)
    full = derive_regimes(bars, min_drawdown=dd)
    for regime in prefix[:-1]:
        assert regime in full
