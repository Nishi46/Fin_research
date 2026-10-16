"""Task 42: attempts to get around DataView.

Direct reads past asof raise LookaheadError. Attacks that go around the guard cannot be
stopped by the view, so each must change the strategy's signal under truncation, which is what
the lookahead stage tests. Pipeline-level assertions are task 43.
"""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from contracts.data import Bar
from contracts.dataview import DataView, LookaheadError
from tests.adversarial._sim import signals_differ_under_truncation
from tests.adversarial.strategies import bypass
from tests.adversarial.worlds import World, noise_world

DAYS = list(range(40, 300, 37))


@pytest.fixture(scope="module")
def world():
    return noise_world(5)


def _view(world: World, i: int) -> tuple[DataView, datetime]:
    asof = world.timestamps[i]
    return DataView(world.provider(), asof), asof


@pytest.mark.parametrize(
    "cls",
    [
        bypass.DirectFutureEnd,
        bypass.FutureCorporateActions,
        bypass.FutureMembership,
        bypass.SlightlyLateEnd,
    ],
)
def test_direct_reads_past_asof_raise(world, cls):
    view, asof = _view(world, 100)
    with pytest.raises(LookaheadError):
        cls(world.symbols).generate(view, asof)


def test_timezone_trick_does_not_slip_past(world):
    from datetime import datetime, timezone

    view, asof = _view(world, 100)
    offset = timezone(timedelta(hours=-12))
    later = (asof + timedelta(hours=1)).astimezone(offset)
    with pytest.raises(LookaheadError):
        view.bars(world.symbols, end=later)
    with pytest.raises((ValueError, TypeError)):
        view.bars(world.symbols, end=datetime(2024, 1, 1))  # naive


def test_leaking_provider_is_caught_on_the_way_out(world):
    class LeakyProvider:
        def __init__(self, inner: Any) -> None:
            self._inner = inner

        def snapshot(self):
            return self._inner.snapshot()

        def bars(self, symbols, end):
            return self._inner.bars(symbols, end + timedelta(days=5))  # ignores `end`

        def corporate_actions(self, symbols, end):
            return self._inner.corporate_actions(symbols, end)

        def membership(self, index, on):
            return self._inner.membership(index, on)

    asof = world.timestamps[100]
    with pytest.raises(LookaheadError):
        DataView(LeakyProvider(world.provider()), asof).bars(world.symbols)


def test_exact_asof_is_allowed_and_one_bar_later_is_not(world):
    view, asof = _view(world, 100)
    assert max(b.ts for b in view.bars(world.symbols, end=asof)) == asof
    with pytest.raises(LookaheadError):
        view.bars(world.symbols, end=world.timestamps[101])


@pytest.mark.parametrize(
    "cls", [bypass.PrivateProviderPeek, bypass.ClockRewrite, bypass.FreshViewOffByOne]
)
def test_guard_cannot_stop_these_so_truncation_must_catch_them(world, cls):
    view, asof = _view(world, 100)
    cls(world.symbols).generate(view, asof)  # no exception: the view is bypassed
    assert view.asof == asof  # and the view itself still reports the right clock
    differing = signals_differ_under_truncation(cls(world.symbols), world, DAYS)
    assert len(differing) == len(DAYS)


def test_peeked_future_really_is_after_asof(world):
    view, asof = _view(world, 100)
    bars: list[Bar] = list(view._provider.bars(world.symbols, asof + timedelta(days=3)))
    assert any(b.ts > asof for b in bars)
    assert asof.tzinfo is UTC
