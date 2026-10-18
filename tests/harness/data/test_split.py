import random
from datetime import UTC, date, datetime, timedelta

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from contracts.data import Bar, CorporateAction, CorporateActionKind
from harness.data.calendar import session_close_utc, trading_sessions
from harness.data.split import split_data
from harness.data.window import compute_windows
from harness.thresholds import DEFAULT_THRESHOLDS, Thresholds
from tests.data_fixtures import series

SESSIONS = trading_sessions(date(2019, 1, 2), date(2021, 12, 31))
LIGHT = Thresholds(holdout_years=1, holdout_embargo_sessions=3, holdout_min_research_years=1)
W = compute_windows(SESSIONS, holdout_years=1, embargo_sessions=3)
CLOSES = [100.0 + (i % 7) * 0.5 for i in range(len(SESSIONS))]
MAIN = series("AAA", CLOSES, start=SESSIONS[0])


def _bar_date(b: Bar) -> date:
    return b.ts.astimezone(UTC).date()  # 16:00 NY is 20:00/21:00 UTC: same calendar date


def _action(
    symbol: str, d: date, kind: CorporateActionKind = CorporateActionKind.DIVIDEND
) -> CorporateAction:
    return CorporateAction(
        symbol=symbol,
        kind=kind,
        effective_at=datetime(d.year, d.month, d.day, tzinfo=UTC),
        value=0.5,
    )


def _dates(bars: list[Bar]) -> list[date]:
    return [_bar_date(b) for b in bars]


def test_sides_follow_windows_and_cover_input() -> None:
    out = split_data(MAIN, [], W, thresholds=LIGHT)
    assert max(_dates(out.research_bars)) == W.research_end
    assert min(_dates(out.holdout_bars)) == W.holdout_start
    assert max(_dates(out.holdout_bars)) == W.holdout_end
    assert len(out.embargo_bars) == W.embargo_sessions
    assert all(W.research_end < d < W.holdout_start for d in _dates(out.embargo_bars))
    joined = out.research_bars + out.embargo_bars + out.holdout_bars
    assert joined == MAIN
    assert out.research_actions == out.embargo_actions == out.holdout_actions == []


def test_boundaries_land_on_the_right_side() -> None:
    first_embargo = SESSIONS[SESSIONS.index(W.research_end) + 1]
    out = split_data(MAIN, [], W, thresholds=LIGHT)
    assert _bar_date(out.research_bars[-1]) == W.research_end
    assert _bar_date(out.embargo_bars[0]) == first_embargo
    assert _bar_date(out.embargo_bars[-1]) == SESSIONS[SESSIONS.index(W.holdout_start) - 1]
    assert _bar_date(out.holdout_bars[0]) == W.holdout_start
    assert _bar_date(out.holdout_bars[-1]) == W.holdout_end


def test_actions_partition_by_utc_date() -> None:
    em = SESSIONS[SESSIONS.index(W.research_end) + 1]
    acts = [
        _action("AAA", W.holdout_end),
        _action("AAA", W.holdout_start),
        _action("AAA", em),
        _action("AAA", W.research_end),
        _action("AAA", W.research_end - timedelta(days=400)),
    ]
    out = split_data(MAIN, acts, W, thresholds=LIGHT)
    assert [a.effective_at.date() for a in out.research_actions] == [
        W.research_end - timedelta(days=400),
        W.research_end,
    ]
    assert [a.effective_at.date() for a in out.embargo_actions] == [em]
    assert [a.effective_at.date() for a in out.holdout_actions] == [W.holdout_start, W.holdout_end]


def test_action_after_holdout_end_raises() -> None:
    late = _action("AAA", W.holdout_end + timedelta(days=1))
    with pytest.raises(ValueError, match="after holdout_end"):
        split_data(MAIN, [late], W, thresholds=LIGHT)


def test_bar_after_holdout_end_raises() -> None:
    extra = series("AAA", [100.0], start=SESSIONS[-1] + timedelta(days=1))
    with pytest.raises(ValueError, match="after holdout_end"):
        split_data(MAIN + extra, [], W, thresholds=LIGHT)


def test_session_date_is_new_york_not_utc() -> None:
    # A 16:00 NY close is same-day in UTC, but a late stamp past UTC midnight must not shift it.
    b = MAIN[10]
    late = b.model_copy(update={"ts": session_close_utc(b.ts.date()) + timedelta(hours=3)})
    # 16:00 NY + 3h = 19:00 NY same NY date (and next UTC date in winter): stays on its session
    assert late.ts.date() >= b.ts.date()
    out = split_data([late, *MAIN[:10], *MAIN[11:]], [], W, thresholds=LIGHT)
    assert late in out.research_bars


def test_empty_bars_refused() -> None:
    with pytest.raises(ValueError, match="no bars"):
        split_data([], [], W, thresholds=LIGHT)


def test_duplicate_bar_refused() -> None:
    with pytest.raises(ValueError, match="duplicate bar"):
        split_data([*MAIN, MAIN[5]], [], W, thresholds=LIGHT)


def test_short_research_refused_with_real_defaults() -> None:
    sessions = trading_sessions(date(2022, 1, 3), date(2025, 12, 31))
    w = compute_windows(
        sessions, DEFAULT_THRESHOLDS.holdout_years, DEFAULT_THRESHOLDS.holdout_embargo_sessions
    )
    bars = series("AAA", [100.0 + (i % 5) for i in range(len(sessions))], start=sessions[0])
    # ~2 years of history before a 2-year holdout is under the default 3-year research minimum
    with pytest.raises(ValueError, match="research side too short"):
        split_data(bars, [], w)


def test_research_exactly_at_minimum_passes() -> None:
    sessions = trading_sessions(date(2019, 1, 2), date(2021, 12, 31))
    w = compute_windows(sessions, 1, 3)
    n_research = sum(1 for s in sessions if s <= w.research_end)
    ok = Thresholds(holdout_years=1, holdout_embargo_sessions=3, holdout_min_research_years=1)
    assert n_research >= 252
    split_data(MAIN, [], w, thresholds=ok)
    # drop the earliest sessions until one short of 252 research sessions
    bars = [b for b in MAIN if _bar_date(b) >= sessions[n_research - 251]]
    with pytest.raises(ValueError, match="research side too short"):
        split_data(bars, [], w, thresholds=ok)


def test_thin_holdout_refused() -> None:
    keep = [b for b in MAIN if _bar_date(b) < W.holdout_start or _bar_date(b).day % 2 == 0]
    holdout_n = len(trading_sessions(W.holdout_start, W.holdout_end))
    got = len({_bar_date(b) for b in keep if _bar_date(b) >= W.holdout_start})
    assert got < 0.9 * holdout_n
    with pytest.raises(ValueError, match="holdout side too thin"):
        split_data(keep, [], W, thresholds=LIGHT)


def test_ipo_in_holdout_and_delisted_before_holdout_allowed() -> None:
    delisted = [
        b.model_copy(update={"symbol": "OLD"}) for b in MAIN if _bar_date(b) <= W.research_end
    ]
    ipo = [b.model_copy(update={"symbol": "NEW"}) for b in MAIN if _bar_date(b) >= W.holdout_start]
    out = split_data(MAIN + delisted + ipo, [], W, thresholds=LIGHT)
    assert {b.symbol for b in out.research_bars} == {"AAA", "OLD"}
    assert {b.symbol for b in out.holdout_bars} == {"AAA", "NEW"}
    assert {b.symbol for b in out.embargo_bars} == {"AAA"}


def test_input_order_does_not_matter_and_output_sorted() -> None:
    other = [b.model_copy(update={"symbol": "BBB"}) for b in MAIN]
    acts = [_action("BBB", W.holdout_start), _action("AAA", W.holdout_start)]
    items = MAIN + other
    shuffled = items[:]
    random.Random(1).shuffle(shuffled)
    a = split_data(items, acts, W, thresholds=LIGHT)
    b = split_data(shuffled, acts[::-1], W, thresholds=LIGHT)
    assert a == b
    for side in (a.research_bars, a.embargo_bars, a.holdout_bars):
        assert side == sorted(side, key=lambda x: (x.ts, x.symbol))
    assert [x.symbol for x in a.holdout_actions] == ["AAA", "BBB"]


@settings(max_examples=25, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(
    idx=st.sets(st.integers(0, len(SESSIONS) - 1), max_size=60),
    seed=st.integers(0, 1000),
    action_idx=st.lists(st.integers(0, len(SESSIONS) - 1), max_size=20),
)
def test_property_exact_partition(idx: set[int], seed: int, action_idx: list[int]) -> None:
    extra = [b.model_copy(update={"symbol": "ZZZ"}) for i, b in enumerate(MAIN) if i in idx]
    acts = [_action("ZZZ", SESSIONS[i]) for i in action_idx]
    items = MAIN + extra
    random.Random(seed).shuffle(items)
    out = split_data(items, acts, W, thresholds=LIGHT)
    got_bars = out.research_bars + out.embargo_bars + out.holdout_bars
    assert sorted(got_bars, key=lambda b: (b.ts, b.symbol)) == sorted(
        items, key=lambda b: (b.ts, b.symbol)
    )
    assert len(got_bars) == len(items)
    got_acts = out.research_actions + out.embargo_actions + out.holdout_actions
    assert len(got_acts) == len(acts)
    assert all(_bar_date(b) <= W.research_end for b in out.research_bars)
    assert all(W.research_end < _bar_date(b) < W.holdout_start for b in out.embargo_bars)
    assert all(W.holdout_start <= _bar_date(b) <= W.holdout_end for b in out.holdout_bars)
