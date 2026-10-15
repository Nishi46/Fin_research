from datetime import timedelta

import pytest

from contracts.data import Bar
from contracts.dataview import DataView
from fakes.data import InMemoryDataProvider
from strategies.baseline import BaselineReversal
from tests.helpers import T0, make_bar


def series(symbol: str, closes: list[float]) -> list[Bar]:
    return [make_bar(symbol, d, close=c) for d, c in enumerate(closes)]


def provider() -> InMemoryDataProvider:
    # LOSER falls hardest over the last 5 bars, WINNER rises, FLAT is flat.
    bars = (
        series("LOSER", [100, 100, 100, 100, 100, 100, 80])
        + series("WINNER", [100, 100, 100, 100, 100, 100, 120])
        + series("FLAT", [100] * 7)
    )
    return InMemoryDataProvider(bars)


def test_buys_the_biggest_recent_losers_with_equal_weights() -> None:
    strategy = BaselineReversal(symbols=["LOSER", "WINNER", "FLAT"], top_n=2, lookback=5)
    asof = T0 + timedelta(days=6)
    intent = strategy.generate(DataView(provider(), asof), asof)
    assert set(intent.weights) == {"LOSER", "FLAT"}
    assert all(w == 0.5 for w in intent.weights.values())


def test_returns_all_cash_without_enough_history() -> None:
    strategy = BaselineReversal(symbols=["LOSER"], top_n=1, lookback=5)
    asof = T0 + timedelta(days=2)
    assert strategy.generate(DataView(provider(), asof), asof).weights == {}


def test_weights_never_exceed_one() -> None:
    strategy = BaselineReversal(symbols=["LOSER", "WINNER", "FLAT"], top_n=3, lookback=5)
    asof = T0 + timedelta(days=6)
    assert sum(strategy.generate(DataView(provider(), asof), asof).weights.values()) <= 1.0


def test_signal_is_unchanged_when_future_data_is_removed() -> None:
    """Lookahead truncation test: signals at t must equal those from the full dataset."""
    asof = T0 + timedelta(days=5)
    full = provider()
    truncated = InMemoryDataProvider([b for b in full.bars(["LOSER", "WINNER", "FLAT"], asof)])
    strategy = BaselineReversal(symbols=["LOSER", "WINNER", "FLAT"], top_n=2, lookback=3)
    assert (
        strategy.generate(DataView(full, asof), asof).weights
        == strategy.generate(DataView(truncated, asof), asof).weights
    )


def test_has_stable_id() -> None:
    assert BaselineReversal(symbols=["A"], top_n=1, lookback=5).strategy_id == "baseline_reversal"


@pytest.mark.parametrize(("top_n", "lookback"), [(0, 5), (1, 0), (-1, 5), (1, -2)])
def test_rejects_nonsensical_parameters(top_n: int, lookback: int) -> None:
    with pytest.raises(ValueError, match="at least 1"):
        BaselineReversal(symbols=["A"], top_n=top_n, lookback=lookback)
