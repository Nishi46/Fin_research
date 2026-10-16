"""Which owner-approved regimes the data actually covers (the regime floor needs this)."""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from contracts.data import Bar
from harness.api import Regime


@dataclass(frozen=True)
class RegimeCoverage:
    sessions: dict[str, int]
    covered: tuple[str, ...]
    missing: tuple[str, ...]
    uncovered_before_table: int
    uncovered_after_table: int


def coverage_report(
    bars: Sequence[Bar], regimes: Sequence[Regime], *, min_sessions: int
) -> RegimeCoverage:
    ordered = sorted(regimes, key=lambda r: r.start)
    stamps: set[datetime] = {b.ts for b in bars}
    counts = {r.name: sum(1 for ts in stamps if r.contains(ts)) for r in ordered}
    first = ordered[0].start if ordered else None
    last = ordered[-1].end if ordered else None
    return RegimeCoverage(
        sessions=counts,
        covered=tuple(r.name for r in ordered if counts[r.name] >= min_sessions),
        missing=tuple(r.name for r in ordered if counts[r.name] < min_sessions),
        uncovered_before_table=sum(1 for ts in stamps if first is not None and ts < first),
        uncovered_after_table=sum(1 for ts in stamps if last is not None and ts >= last),
    )


_DRAWDOWN_TOLERANCE = 1e-12


def _midnight(day: date) -> datetime:
    return datetime(day.year, day.month, day.day, tzinfo=UTC)


def derive_regimes(bars: Sequence[Bar], *, min_drawdown: float) -> tuple[Regime, ...]:
    """Derive selloff/advance regimes from one symbol's daily closes (pure, deterministic).

    A selloff runs from a record close to the lowest close before the next record, if it fell
    at least `min_drawdown`. Stretches between selloffs are advances. An episode with no new
    record yet is open and provisional (`_open` suffix, ends one day after the last bar).
    Completed episodes never change when later bars are appended; only the last regime can.
    """
    if not bars:
        raise ValueError("bars is empty")
    if len(bars) < 2:
        raise ValueError("need at least 2 bars")
    if len({b.symbol for b in bars}) != 1:
        raise ValueError("bars must be for one symbol")
    if not 0.0 < min_drawdown < 1.0:
        raise ValueError("min_drawdown must be in (0, 1)")
    ordered = sorted(bars, key=lambda b: b.ts)
    days = [b.ts.astimezone(UTC).date() for b in ordered]
    if len(set(days)) != len(days):
        raise ValueError("two bars on the same date")
    closes = [b.close for b in ordered]

    # Each episode is (peak_idx, end_idx_exclusive_or_None); end is the next record's index.
    episodes: list[tuple[int, int | None]] = []
    record_idx = 0
    peak_open: int | None = None
    for i in range(1, len(closes)):
        if closes[i] > closes[record_idx]:
            if peak_open is not None:
                episodes.append((peak_open, i))
                peak_open = None
            record_idx = i
        elif peak_open is None:
            drawdown = 1.0 - closes[i] / closes[record_idx]
            if drawdown >= min_drawdown - _DRAWDOWN_TOLERANCE:
                peak_open = record_idx
    if peak_open is not None:
        episodes.append((peak_open, None))

    final_end = _midnight(days[-1] + timedelta(days=1))
    regimes: list[Regime] = []
    cursor = _midnight(days[0])

    def add(name: str, start: datetime, end: datetime) -> None:
        if end > start:
            regimes.append(Regime(name=name, start=start, end=end))

    for peak, end_idx in episodes:
        peak_ts = _midnight(days[peak])
        add(f"advance_{cursor:%Y-%m-%d}", cursor, peak_ts)
        if end_idx is None:
            add(f"selloff_{peak_ts:%Y-%m-%d}_open", peak_ts, final_end)
            return tuple(regimes)
        trough = min(range(peak + 1, end_idx), key=lambda j: (closes[j], j))
        trough_ts = _midnight(days[trough])
        add(f"selloff_{peak_ts:%Y-%m-%d}", peak_ts, trough_ts)
        cursor = trough_ts
    add(f"advance_{cursor:%Y-%m-%d}", cursor, final_end)
    return tuple(regimes)
