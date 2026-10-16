"""NYSE session helpers. Bars are stamped at the regular 16:00 New York close."""

from datetime import UTC, date, datetime, time
from functools import lru_cache
from typing import Any
from zoneinfo import ZoneInfo

import exchange_calendars as xcals  # type: ignore[import-untyped]
import pandas as pd  # type: ignore[import-untyped]

_NY = ZoneInfo("America/New_York")


@lru_cache(maxsize=1)
def _calendar() -> Any:
    return xcals.get_calendar("XNYS")


def trading_sessions(start: date, end: date) -> list[date]:
    """NYSE sessions in [start, end], clipped to the range the calendar library covers."""
    cal = _calendar()
    lo = max(pd.Timestamp(start), cal.first_session.tz_localize(None))
    hi = min(pd.Timestamp(end), cal.last_session.tz_localize(None))
    if lo > hi:
        return []
    return [ts.date() for ts in cal.sessions_in_range(lo, hi)]


def session_close_utc(session: date) -> datetime:
    return datetime.combine(session, time(16, 0), tzinfo=_NY).astimezone(UTC)
