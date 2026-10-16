"""SYNTHETIC recorded fixtures for harness/data tests. None of this is real market data.

Hand-made JSON/CSV in this directory mimic the shape of vendor responses so tests never
touch the network. Series builders below produce bars on real NYSE sessions.
"""

import itertools
import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from contracts.data import Bar
from harness.data.calendar import session_close_utc, trading_sessions

FIXTURE_DIR = Path(__file__).parent
FIRST_SESSION = date(2026, 1, 5)


def load_json(name: str) -> dict[str, Any]:
    data = json.loads((FIXTURE_DIR / name).read_text())
    assert isinstance(data, dict)
    return data


def load_text(name: str) -> str:
    return (FIXTURE_DIR / name).read_text()


def series(
    symbol: str,
    closes: list[float],
    *,
    start: date = FIRST_SESSION,
    volume: float = 1_000_000.0,
) -> list[Bar]:
    """One bar per NYSE session; each bar gets a distinct open/high/low so it is not stale."""
    sessions = trading_sessions(start, start + timedelta(days=len(closes) * 2 + 10))[: len(closes)]
    bars: list[Bar] = []
    previous = closes[0]
    for session, close in zip(sessions, closes, strict=True):
        bars.append(
            Bar(
                symbol=symbol,
                ts=session_close_utc(session),
                open=previous,
                high=max(previous, close) * 1.002,
                low=min(previous, close) * 0.998,
                close=close,
                volume=volume + session.day,
            )
        )
        previous = close
    return bars


def normal_series(symbol: str = "AAA", n: int = 20) -> list[Bar]:
    return series(symbol, [100.0 + 0.5 * i + (i % 3) * 0.3 for i in range(n)])


def raw_split_series(symbol: str = "SPL") -> list[Bar]:
    """Raw (unadjusted) prices with a 2-for-1 split effective on the 5th session (index 4)."""
    return series(symbol, [100.0, 101.0, 102.0, 101.5, 51.0, 51.5, 52.0, 51.8])


def split_ex_date(start: date = FIRST_SESSION) -> datetime:
    sessions = trading_sessions(start, start + timedelta(days=30))
    return datetime(sessions[4].year, sessions[4].month, sessions[4].day, tzinfo=UTC)


def dividend_series(symbol: str = "DIV") -> list[Bar]:
    return series(symbol, [100.0, 100.0, 99.0, 99.5, 100.0, 100.5])


def dividend_ex_date(start: date = FIRST_SESSION) -> datetime:
    sessions = trading_sessions(start, start + timedelta(days=30))
    return datetime(sessions[2].year, sessions[2].month, sessions[2].day, tzinfo=UTC)


def gapped_series(symbol: str = "GAP") -> list[Bar]:
    bars = series(symbol, [100.0 + i for i in range(10)])
    return [b for i, b in enumerate(bars) if i not in (3, 4)]


def stale_series(symbol: str = "STL") -> list[Bar]:
    bars = series(symbol, [100.0 + i for i in range(8)])
    frozen = bars[3]
    out = bars[:4]
    for later in bars[4:7]:
        out.append(frozen.model_copy(update={"ts": later.ts, "volume": 0.0}))
    out.append(bars[7])
    return out


def disagreeing_source(bars: list[Bar], *, index: int = 5, factor: float = 1.08) -> list[Bar]:
    """A second 'vendor' whose close at `index` is off by `factor`."""
    out = list(bars)
    b = out[index]
    c = b.close * factor
    out[index] = b.model_copy(update={"close": c, "high": max(b.high, c), "low": min(b.low, c)})
    return out


ANCHOR_KEYS: list[tuple[date, float]] = [
    (date(2018, 9, 20), 2930.75),
    (date(2018, 12, 24), 2351.10),
    (date(2020, 2, 19), 3386.15),
    (date(2020, 3, 23), 2237.40),
    (date(2022, 1, 3), 4796.56),
    (date(2022, 10, 12), 3577.03),
    (date(2025, 2, 19), 6144.15),
    (date(2025, 4, 8), 4982.77),
    (date(2025, 6, 30), 6200.00),
]


def sp500_anchor_series(symbol: str = "SPY") -> list[Bar]:
    """Closes pass through real published S&P 500 peak/trough values on the anchor dates.

    The path between those dates is a synthetic straight line on real NYSE sessions, followed
    by three rising days. It is not market data.
    """
    sessions = trading_sessions(ANCHOR_KEYS[0][0], date(2025, 7, 3))
    key_index = {d: sessions.index(d) for d, _ in ANCHOR_KEYS}
    closes: list[float] = []
    for (d0, c0), (d1, c1) in itertools.pairwise(ANCHOR_KEYS):
        i0, i1 = key_index[d0], key_index[d1]
        closes.extend(c0 + (c1 - c0) * (i - i0) / (i1 - i0) for i in range(i0, i1))
    last = ANCHOR_KEYS[-1][1]
    closes.extend(last + 10.0 * k for k in range(len(sessions) - len(closes)))
    return series(symbol, closes, start=ANCHOR_KEYS[0][0])
