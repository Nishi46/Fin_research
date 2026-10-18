"""Partition bars and corporate actions into research / embargo / holdout by session date."""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date
from zoneinfo import ZoneInfo

from contracts.data import Bar, CorporateAction
from harness.data.calendar import trading_sessions
from harness.data.window import Windows
from harness.thresholds import DEFAULT_THRESHOLDS, Thresholds

_NY = ZoneInfo("America/New_York")
_SESSIONS_PER_YEAR = 252


@dataclass(frozen=True)
class SplitData:
    research_bars: list[Bar]
    research_actions: list[CorporateAction]
    embargo_bars: list[Bar]
    embargo_actions: list[CorporateAction]
    holdout_bars: list[Bar]
    holdout_actions: list[CorporateAction]


def _bar_session(bar: Bar) -> date:
    # Bars are stamped at the NY close, so the NY calendar date is the session.
    return bar.ts.astimezone(_NY).date()


def _action_session(action: CorporateAction) -> date:
    # Actions are stamped at midnight UTC of the ex-date.
    return action.effective_at.astimezone(UTC).date()


def _side(d: date, windows: Windows) -> str:
    if d > windows.holdout_end:
        raise ValueError(f"data dated {d} is after holdout_end {windows.holdout_end}")
    if d <= windows.research_end:
        return "research"
    if d < windows.holdout_start:
        return "embargo"
    return "holdout"


def split_data(
    bars: Sequence[Bar],
    actions: Sequence[CorporateAction],
    windows: Windows,
    *,
    thresholds: Thresholds = DEFAULT_THRESHOLDS,
) -> SplitData:
    if not bars:
        raise ValueError("no bars to split")
    ordered_bars = sorted(bars, key=lambda b: (b.ts, b.symbol))
    seen: set[tuple[str, object]] = set()
    for b in ordered_bars:
        key = (b.symbol, b.ts)
        if key in seen:
            raise ValueError(f"duplicate bar for {b.symbol} at {b.ts}")
        seen.add(key)
    ordered_actions = sorted(actions, key=lambda a: (a.effective_at, a.symbol))

    buckets_b: dict[str, list[Bar]] = {"research": [], "embargo": [], "holdout": []}
    buckets_a: dict[str, list[CorporateAction]] = {"research": [], "embargo": [], "holdout": []}
    dates: dict[str, set[date]] = {"research": set(), "embargo": set(), "holdout": set()}
    for b in ordered_bars:
        d = _bar_session(b)
        side = _side(d, windows)
        buckets_b[side].append(b)
        dates[side].add(d)
    for a in ordered_actions:
        buckets_a[_side(_action_session(a), windows)].append(a)

    min_research = thresholds.holdout_min_research_years * _SESSIONS_PER_YEAR
    if len(dates["research"]) < min_research:
        raise ValueError(
            f"research side too short: {len(dates['research'])} distinct sessions, "
            f"need at least {thresholds.holdout_min_research_years} years ({min_research})"
        )
    expected = len(trading_sessions(windows.holdout_start, windows.holdout_end))
    min_holdout = thresholds.holdout_min_session_fraction * expected
    if len(dates["holdout"]) < min_holdout:
        raise ValueError(
            f"holdout side too thin: {len(dates['holdout'])} distinct sessions of {expected} "
            f"expected, need at least {thresholds.holdout_min_session_fraction:.0%}"
        )
    return SplitData(
        research_bars=buckets_b["research"],
        research_actions=buckets_a["research"],
        embargo_bars=buckets_b["embargo"],
        embargo_actions=buckets_a["embargo"],
        holdout_bars=buckets_b["holdout"],
        holdout_actions=buckets_a["holdout"],
    )
