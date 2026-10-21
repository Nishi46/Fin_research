from collections.abc import Sequence
from datetime import date, datetime

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from contracts.data import (
    Bar,
    CorporateAction,
    CorporateActionKind,
    DataProvider,
    Snapshot,
)
from harness.data.calendar import session_close_utc, trading_sessions
from harness.data.membership import IndexHistory, MembershipChange, PointInTimeMembership
from harness.data.provider import ComposedDataProvider
from harness.data.window import ResearchWindow, Windows, compute_windows
from tests.data_fixtures import FIRST_SESSION, series

SESSIONS = trading_sessions(date(2016, 1, 1), date(2024, 12, 31))


def _between(w: Windows) -> int:
    return sum(1 for s in SESSIONS if w.research_end < s < w.holdout_start)


def test_known_range_expected_dates() -> None:
    sessions = trading_sessions(date(2018, 1, 1), date(2024, 12, 31))
    w = compute_windows(sessions, holdout_years=2, embargo_sessions=5)
    assert w.holdout_end == date(2024, 12, 31)
    assert w.holdout_start == date(2023, 1, 3)  # cutoff 2022-12-31 is Saturday, Jan 2 is a holiday
    assert w.research_end == date(2022, 12, 22)
    assert w.embargo_sessions == 5


def test_exactly_embargo_sessions_between() -> None:
    for n in (1, 5, 10):
        w = compute_windows(SESSIONS, holdout_years=2, embargo_sessions=n)
        assert _between(w) == n


def test_holdout_start_is_a_session_and_on_or_after_cutoff() -> None:
    w = compute_windows(SESSIONS, holdout_years=3, embargo_sessions=5)
    assert w.holdout_start in set(SESSIONS)
    assert w.holdout_start >= date(2021, 12, 31)


def test_leap_day_end_date() -> None:
    sessions = trading_sessions(date(2018, 1, 1), date(2024, 2, 29))
    w = compute_windows(sessions, holdout_years=1, embargo_sessions=3)
    assert w.holdout_end == date(2024, 2, 29)
    assert w.holdout_start == date(2023, 2, 28)
    assert w.holdout_start in set(sessions)


def test_ordering() -> None:
    w = compute_windows(SESSIONS, holdout_years=2, embargo_sessions=5)
    assert w.research_end < w.holdout_start < w.holdout_end


def test_too_short_history_raises() -> None:
    short = trading_sessions(date(2024, 1, 1), date(2024, 12, 31))
    with pytest.raises(ValueError, match="too few sessions"):
        compute_windows(short, holdout_years=1, embargo_sessions=5)


def test_empty_raises() -> None:
    with pytest.raises(ValueError, match="empty"):
        compute_windows([], holdout_years=1, embargo_sessions=1)


def test_unsorted_or_duplicate_raises() -> None:
    s = SESSIONS[-600:]
    with pytest.raises(ValueError, match="sorted"):
        compute_windows(list(reversed(s)), holdout_years=1, embargo_sessions=1)
    with pytest.raises(ValueError, match="sorted"):
        compute_windows([*s, s[-1]], holdout_years=1, embargo_sessions=1)


@pytest.mark.parametrize(("years", "embargo"), [(0, 5), (1, 0), (-1, 5), (1, -2)])
def test_bad_params_raise(years: int, embargo: int) -> None:
    with pytest.raises(ValueError, match=">= 1"):
        compute_windows(SESSIONS, holdout_years=years, embargo_sessions=embargo)


def test_windows_is_frozen() -> None:
    w = compute_windows(SESSIONS, holdout_years=2, embargo_sessions=5)
    with pytest.raises(AttributeError):
        w.research_end = date(2020, 1, 1)  # type: ignore[misc]


@settings(max_examples=50, deadline=None)
@given(
    years=st.integers(min_value=1, max_value=5),
    embargo=st.integers(min_value=1, max_value=30),
    end_idx=st.integers(min_value=1500, max_value=len(SESSIONS) - 1),
)
def test_property_embargo_and_order(years: int, embargo: int, end_idx: int) -> None:
    w = compute_windows(SESSIONS[: end_idx + 1], holdout_years=years, embargo_sessions=embargo)
    assert w.research_end < w.holdout_start <= w.holdout_end
    assert sum(1 for s in SESSIONS if w.research_end < s < w.holdout_start) == embargo


# --- ResearchWindow -------------------------------------------------------------------

_N = 30
_ALL_SESSIONS = trading_sessions(FIRST_SESSION, date(2026, 6, 30))[:_N]
_CUT = _ALL_SESSIONS[14]
_CUT_CLOSE = session_close_utc(_CUT)
_LAST_CLOSE = session_close_utc(_ALL_SESSIONS[-1])
_AFTER = _ALL_SESSIONS[20]
_SPLIT_AFTER = CorporateAction(
    symbol="AAA",
    kind=CorporateActionKind.SPLIT,
    effective_at=session_close_utc(_AFTER),
    value=2.0,
)
_DIV_BEFORE = CorporateAction(
    symbol="AAA",
    kind=CorporateActionKind.DIVIDEND,
    effective_at=session_close_utc(_ALL_SESSIONS[3]),
    value=0.5,
)
_MEMBERSHIP = PointInTimeMembership(
    {
        "IDX": IndexHistory(
            current=frozenset({"AAA", "NEW"}),
            changes=(MembershipChange(on=_AFTER, added=frozenset({"NEW"}), removed=frozenset()),),
        )
    }
)


def _closes() -> list[float]:
    return [100.0 + 0.5 * i + (i % 3) * 0.3 for i in range(_N)]


def _inner() -> ComposedDataProvider:
    return ComposedDataProvider(
        series("AAA", _closes()),
        corporate_actions=[_DIV_BEFORE, _SPLIT_AFTER],
        membership=_MEMBERSHIP,
    )


class _HostileProvider:
    """Ignores `end` and `on`; returns everything it has."""

    def __init__(self, inner: ComposedDataProvider) -> None:
        self._inner = inner

    def snapshot(self) -> Snapshot:
        return self._inner.snapshot()

    def bars(self, symbols: Sequence[str], end: datetime) -> Sequence[Bar]:
        return self._inner.bars(symbols, _LAST_CLOSE)

    def corporate_actions(self, symbols: Sequence[str], end: datetime) -> Sequence[CorporateAction]:
        return self._inner.corporate_actions(symbols, _LAST_CLOSE)

    def membership(self, index: str, on: date) -> frozenset[str]:
        return self._inner.membership(index, date(2030, 1, 1))


def _bars_leak(w: DataProvider) -> None:
    got = w.bars(["AAA"], _LAST_CLOSE)
    assert got
    assert all(b.ts <= _CUT_CLOSE for b in got)
    assert max(b.ts for b in got) == _CUT_CLOSE


def _actions_leak(w: DataProvider) -> None:
    got = w.corporate_actions(["AAA"], _LAST_CLOSE)
    assert got == [_DIV_BEFORE]


def _membership_leak(w: DataProvider) -> None:
    assert w.membership("IDX", date(2030, 1, 1)) == frozenset({"AAA"})


def _snapshot_leak(w: DataProvider) -> None:
    assert w.snapshot().end <= _CUT_CLOSE


LEAK_CHECKS = {
    "bars": _bars_leak,
    "corporate_actions": _actions_leak,
    "membership": _membership_leak,
    "snapshot": _snapshot_leak,
}


@pytest.mark.parametrize("hostile", [False, True], ids=["composed", "hostile"])
@pytest.mark.parametrize("method", sorted(LEAK_CHECKS))
def test_no_method_leaks_past_research_end(method: str, hostile: bool) -> None:
    if hostile and method == "membership":
        # Membership has no timestamps to filter: the wrapper can only clamp `on`.
        pytest.skip("membership is clamp-only; a hostile inner cannot be filtered")
    inner: DataProvider = _HostileProvider(_inner()) if hostile else _inner()
    LEAK_CHECKS[method](ResearchWindow(inner, _CUT))


def test_every_protocol_method_is_covered() -> None:
    public = {
        n
        for n in vars(DataProvider)
        if not n.startswith("_") and callable(getattr(DataProvider, n))
    }
    assert public == set(LEAK_CHECKS)
    for name in public:
        assert callable(getattr(ResearchWindow, name))


def test_request_spanning_cutoff_is_truncated() -> None:
    w = ResearchWindow(_inner(), _CUT)
    got = w.bars(["AAA"], session_close_utc(_ALL_SESSIONS[10]))
    assert max(b.ts for b in got) == session_close_utc(_ALL_SESSIONS[10])
    got = w.bars(["AAA"], _LAST_CLOSE)
    assert len(got) == 15


def test_request_entirely_after_cutoff_is_empty_for_other_symbol_window() -> None:
    inner = ComposedDataProvider(series("AAA", _closes(), start=_AFTER))
    w = ResearchWindow(inner, _CUT)
    assert w.bars(["AAA"], _LAST_CLOSE) == []
    assert w.corporate_actions(["AAA"], _LAST_CLOSE) == []


def test_hostile_provider_bars_empty_when_all_after() -> None:
    inner = _HostileProvider(ComposedDataProvider(series("AAA", _closes(), start=_AFTER)))
    assert ResearchWindow(inner, _CUT).bars(["AAA"], _LAST_CLOSE) == []


def test_membership_before_cutoff_passes_through() -> None:
    w = ResearchWindow(_inner(), _CUT)
    assert w.membership("IDX", _ALL_SESSIONS[2]) == _inner().membership("IDX", _ALL_SESSIONS[2])


def test_snapshot_end_capped_and_id_unchanged() -> None:
    inner = _inner()
    snap = ResearchWindow(inner, _CUT).snapshot()
    assert snap.end == _CUT_CLOSE
    assert snap.id == inner.snapshot().id
    assert snap.start == inner.snapshot().start


def test_snapshot_never_extended() -> None:
    inner = ComposedDataProvider(series("AAA", _closes()[:5]))
    snap = ResearchWindow(inner, _CUT).snapshot()
    assert snap.end == inner.snapshot().end < _CUT_CLOSE


def test_research_end_must_be_a_date() -> None:
    with pytest.raises(TypeError):
        ResearchWindow(_inner(), datetime(2026, 2, 1))  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        ResearchWindow(_inner(), "2026-02-01")  # type: ignore[arg-type]


def test_inner_not_mutated_and_usable_as_data_provider() -> None:
    inner = _inner()
    before = inner.bars(["AAA"], _LAST_CLOSE)
    w: DataProvider = ResearchWindow(inner, _CUT)
    w.bars(["AAA"], _LAST_CLOSE)
    assert inner.bars(["AAA"], _LAST_CLOSE) == before


def test_research_end_is_exposed_read_only() -> None:
    w = ResearchWindow(_inner(), _CUT)
    assert w.research_end == _CUT
    with pytest.raises(AttributeError):
        w.research_end = date(2000, 1, 1)  # type: ignore[misc]
