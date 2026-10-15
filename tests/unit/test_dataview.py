from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta

import pytest
from hypothesis import given
from hypothesis import strategies as st

from contracts.data import Bar, CorporateAction, DataProvider, Snapshot
from contracts.dataview import DataView, LookaheadError
from contracts.intent import Intent

T0 = datetime(2026, 1, 5, 21, 0, tzinfo=UTC)


def bar(symbol: str, ts: datetime, close: float = 100.0) -> Bar:
    return Bar(symbol=symbol, ts=ts, open=close, high=close, low=close, close=close, volume=1_000.0)


class HonestProvider:
    def __init__(self, bars: Sequence[Bar]) -> None:
        self._bars = list(bars)

    def snapshot(self) -> Snapshot:
        return Snapshot(id="snap-test", survivorship_biased=True, start=T0, end=T0)

    def bars(self, symbols: Sequence[str], end: datetime) -> Sequence[Bar]:
        return [b for b in self._bars if b.symbol in symbols and b.ts <= end]

    def corporate_actions(self, symbols: Sequence[str], end: datetime) -> Sequence[CorporateAction]:
        return []

    def membership(self, index: str, on: date) -> frozenset[str]:
        return frozenset({"AAA"})


class LeakyProvider(HonestProvider):
    """Ignores `end` and returns everything: a buggy or malicious provider."""

    def bars(self, symbols: Sequence[str], end: datetime) -> Sequence[Bar]:
        return [b for b in self._bars if b.symbol in symbols]


def test_provider_fake_satisfies_protocol() -> None:
    provider: DataProvider = HonestProvider([])
    assert provider.snapshot().survivorship_biased is True


def test_returns_only_bars_up_to_asof() -> None:
    provider = HonestProvider([bar("AAA", T0 - timedelta(days=1)), bar("AAA", T0)])
    view = DataView(provider, asof=T0 - timedelta(days=1))
    assert [b.ts for b in view.bars(["AAA"])] == [T0 - timedelta(days=1)]


def test_explicit_end_after_asof_raises() -> None:
    view = DataView(HonestProvider([]), asof=T0)
    with pytest.raises(LookaheadError):
        view.bars(["AAA"], end=T0 + timedelta(days=1))


def test_leaky_provider_is_caught() -> None:
    provider = LeakyProvider([bar("AAA", T0), bar("AAA", T0 + timedelta(days=1))])
    view = DataView(provider, asof=T0)
    with pytest.raises(LookaheadError):
        view.bars(["AAA"])


def test_membership_after_asof_date_raises() -> None:
    view = DataView(HonestProvider([]), asof=T0)
    with pytest.raises(LookaheadError):
        view.membership("SP500", T0.date() + timedelta(days=1))
    assert view.membership("SP500", T0.date()) == frozenset({"AAA"})


def test_corporate_actions_end_after_asof_raises() -> None:
    view = DataView(HonestProvider([]), asof=T0)
    with pytest.raises(LookaheadError):
        view.corporate_actions(["AAA"], end=T0 + timedelta(seconds=1))


def test_rejects_naive_asof() -> None:
    with pytest.raises(ValueError, match="timezone"):
        DataView(HonestProvider([]), asof=datetime(2026, 1, 5, 21, 0))


class PeekingStrategy:
    strategy_id = "peeker"

    def generate(self, data: DataView, asof: datetime) -> Intent:
        data.bars(["AAA"], end=asof + timedelta(days=1))
        return Intent(asof=asof, weights={})


def test_peeking_strategy_cannot_see_the_future() -> None:
    view = DataView(HonestProvider([bar("AAA", T0 + timedelta(days=1))]), asof=T0)
    with pytest.raises(LookaheadError):
        PeekingStrategy().generate(view, T0)


@given(
    offsets=st.lists(st.integers(min_value=-30, max_value=30), min_size=1, max_size=20),
    asof_offset=st.integers(min_value=-30, max_value=30),
)
def test_property_no_returned_bar_is_after_asof(offsets: list[int], asof_offset: int) -> None:
    bars = [bar("AAA", T0 + timedelta(days=o)) for o in offsets]
    asof = T0 + timedelta(days=asof_offset)
    view = DataView(HonestProvider(bars), asof=asof)
    assert all(b.ts <= asof for b in view.bars(["AAA"]))


@pytest.mark.parametrize(
    ("high", "low", "open_", "close"),
    [
        (99.0, 101.0, 100.0, 100.0),  # high below low
        (100.0, 90.0, 100.0, 105.0),  # high below close
        (110.0, 105.0, 100.0, 106.0),  # low above open
    ],
)
def test_bar_rejects_inconsistent_ohlc(high: float, low: float, open_: float, close: float) -> None:
    with pytest.raises(ValueError, match="OHLC"):
        Bar(symbol="AAA", ts=T0, open=open_, high=high, low=low, close=close, volume=1.0)
