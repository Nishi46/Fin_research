"""Holdout window arithmetic and the research-side clamp."""

import itertools
from bisect import bisect_left
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime

from contracts.data import Bar, CorporateAction, DataProvider, Snapshot
from harness.data.calendar import session_close_utc


@dataclass(frozen=True)
class Windows:
    research_end: date
    holdout_start: date
    holdout_end: date
    embargo_sessions: int


def _years_before(d: date, years: int) -> date:
    try:
        return d.replace(year=d.year - years)
    except ValueError:  # Feb 29 in a non-leap target year
        return d.replace(year=d.year - years, day=28)


def compute_windows(sessions: Sequence[date], holdout_years: int, embargo_sessions: int) -> Windows:
    """Split sorted NYSE sessions into research / embargo / holdout.

    Exactly `embargo_sessions` sessions lie strictly between research_end and holdout_start.
    """
    if holdout_years < 1 or embargo_sessions < 1:
        raise ValueError(
            "holdout_years and embargo_sessions must be >= 1 "
            f"(got {holdout_years}, {embargo_sessions})"
        )
    if not sessions:
        raise ValueError("sessions is empty")
    if any(a >= b for a, b in itertools.pairwise(sessions)):
        raise ValueError("sessions must be strictly sorted ascending with no duplicates")
    holdout_end = sessions[-1]
    cutoff = _years_before(holdout_end, holdout_years)
    i = bisect_left(sessions, cutoff)
    if i - embargo_sessions - 1 < 0:
        raise ValueError(
            f"too few sessions: need history before {cutoff} plus {embargo_sessions} "
            f"embargo sessions and one research session; first available is {sessions[0]}"
        )
    return Windows(
        research_end=sessions[i - embargo_sessions - 1],
        holdout_start=sessions[i],
        holdout_end=holdout_end,
        embargo_sessions=embargo_sessions,
    )


class ResearchWindow:
    """DataProvider wrapper: nothing dated after `research_end` can come out, whatever is asked.

    The inner provider is expected to be built from already-capped data, so its snapshot id is
    kept as-is; this class only clamps queries and filters results as defence in depth.
    """

    def __init__(self, inner: DataProvider, research_end: date) -> None:
        if type(research_end) is not date:
            raise TypeError(f"research_end must be a date, got {type(research_end).__name__}")
        self._inner = inner
        self._research_end = research_end
        self._cap = session_close_utc(research_end)

    def snapshot(self) -> Snapshot:
        snap = self._inner.snapshot()
        if snap.end <= self._cap:
            return snap
        return snap.model_copy(update={"end": self._cap})

    def bars(self, symbols: Sequence[str], end: datetime) -> list[Bar]:
        return [b for b in self._inner.bars(symbols, min(end, self._cap)) if b.ts <= self._cap]

    def corporate_actions(self, symbols: Sequence[str], end: datetime) -> list[CorporateAction]:
        got = self._inner.corporate_actions(symbols, min(end, self._cap))
        return [a for a in got if a.effective_at <= self._cap]

    def membership(self, index: str, on: date) -> frozenset[str]:
        return self._inner.membership(index, min(on, self._research_end))
