from datetime import date

import pandas as pd  # type: ignore[import-untyped]
import pytest

from harness.data.crosscheck import YFinanceCrossChecker
from harness.data.ratelimit import RateLimiter
from harness.data.yfinance_fetcher import make_yfinance_fetcher
from tests.data_fixtures import series


def frame(rows: list[tuple[str, float, float]]) -> pd.DataFrame:
    """(date, close, volume) rows shaped like yfinance history: tz-aware index, capitalised."""
    index = pd.DatetimeIndex([pd.Timestamp(d, tz="America/New_York") for d, _, _ in rows])
    return pd.DataFrame(
        {
            "Open": [c for _, c, _ in rows],
            "High": [c for _, c, _ in rows],
            "Low": [c for _, c, _ in rows],
            "Close": [c for _, c, _ in rows],
            "Volume": [v for _, _, v in rows],
        },
        index=index,
    )


def limiter() -> RateLimiter:
    return RateLimiter(max_calls=1000, period_s=60.0, sleep=lambda _s: None)


def test_rows_have_the_columns_the_cross_checker_reads() -> None:
    fetch = make_yfinance_fetcher(
        limiter(), history=lambda s, a, b: frame([("2026-10-05", 100.0, 1e6)])
    )
    rows = list(fetch("SPY", date(2026, 10, 5), date(2026, 10, 5)))
    assert rows == [
        {
            "Date": "2026-10-05",
            "Open": 100.0,
            "High": 100.0,
            "Low": 100.0,
            "Close": 100.0,
            "Volume": 1e6,
        }
    ]


def test_end_date_is_inclusive_because_yfinance_end_is_exclusive() -> None:
    seen: list[tuple[str, date, date]] = []

    def history(symbol: str, start: date, end: date) -> pd.DataFrame:
        seen.append((symbol, start, end))
        return frame([])

    make_yfinance_fetcher(limiter(), history=history)("SPY", date(2026, 10, 1), date(2026, 10, 16))
    assert seen == [("SPY", date(2026, 10, 1), date(2026, 10, 17))]


def test_rows_with_missing_prices_are_dropped_not_zero_filled() -> None:
    data = frame([("2026-10-05", 100.0, 1e6), ("2026-10-06", 101.0, 1e6)])
    data.loc[data.index[1], "Close"] = float("nan")
    fetch = make_yfinance_fetcher(limiter(), history=lambda s, a, b: data)
    assert [r["Date"] for r in fetch("SPY", date(2026, 10, 5), date(2026, 10, 6))] == ["2026-10-05"]


def test_every_call_goes_through_the_rate_limiter() -> None:
    sleeps: list[float] = []
    now = [0.0]
    slow = RateLimiter(max_calls=1, period_s=60.0, clock=lambda: now[0], sleep=sleeps.append)
    fetch = make_yfinance_fetcher(slow, history=lambda s, a, b: frame([]))
    fetch("SPY", date(2026, 10, 5), date(2026, 10, 6))
    fetch("AAPL", date(2026, 10, 5), date(2026, 10, 6))
    assert len(sleeps) == 1


def test_vendor_errors_propagate_instead_of_looking_like_no_data() -> None:
    def boom(symbol: str, start: date, end: date) -> pd.DataFrame:
        raise RuntimeError("yahoo says no")

    with pytest.raises(RuntimeError, match="yahoo says no"):
        make_yfinance_fetcher(limiter(), history=boom)("SPY", date(2026, 10, 5), date(2026, 10, 6))


def test_plugs_into_the_cross_checker() -> None:
    fetch = make_yfinance_fetcher(
        limiter(), history=lambda s, a, b: frame([("2026-10-05", 100.0, 1e6)])
    )
    primary = series("SPY", [100.0], start=date(2026, 10, 5))
    report = YFinanceCrossChecker(fetch).check(primary, ["SPY"])
    assert report.ok
    assert report.compared == 1
