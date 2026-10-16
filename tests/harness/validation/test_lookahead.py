from datetime import datetime, timedelta

import pytest

from contracts import Stage
from contracts.data import DataProvider
from contracts.dataview import DataView
from contracts.intent import Intent
from fakes.data import InMemoryDataProvider
from harness.validation.lookahead import TruncatedProvider, check_lookahead
from strategies.baseline import BaselineReversal
from tests.helpers import T0, make_bar

SYMBOLS = ["AAA", "BBB", "CCC"]
N_DAYS = 40


def provider() -> InMemoryDataProvider:
    bars = []
    for i, symbol in enumerate(SYMBOLS):
        for day in range(N_DAYS):
            # Deterministic, differently shaped paths so rankings change over time.
            close = 100.0 + 5.0 * ((day * (i + 2)) % 7) + day * 0.1 * (i - 1)
            bars.append(make_bar(symbol, day, close=close))
    return InMemoryDataProvider(bars)


def times(days: range) -> list[datetime]:
    return [T0 + timedelta(days=d) for d in days]


class PeekingStrategy:
    """Cheats by reaching through the view to the provider: buys tomorrow's biggest winner."""

    strategy_id = "peek"

    def generate(self, data: DataView, asof: datetime) -> Intent:
        peek = data._provider
        future = {b.symbol: b for b in peek.bars(SYMBOLS, asof + timedelta(days=1)) if b.ts > asof}
        if not future:
            return Intent(asof=asof, weights={})
        best = max(future, key=lambda s: (future[s].close, s))
        return Intent(asof=asof, weights={best: 1.0})


class AskingStrategy:
    """Honest-looking cheat: asks the view for data past asof (the view raises)."""

    strategy_id = "ask"

    def generate(self, data: DataView, asof: datetime) -> Intent:
        data.bars(SYMBOLS, asof + timedelta(days=1))
        return Intent(asof=asof, weights={})


class FullHistoryNormalizer:
    """Subtle leak: ranks symbols by the newest bar the provider holds, not the newest <= asof."""

    strategy_id = "normalizer"

    def generate(self, data: DataView, asof: datetime) -> Intent:
        peek = data._provider
        everything = peek.bars(SYMBOLS, asof + timedelta(days=365))
        newest = max(b.ts for b in everything)
        top = max((b for b in everything if b.ts == newest), key=lambda b: (b.close, b.symbol))
        return Intent(asof=asof, weights={top.symbol: 0.5})


def test_baseline_passes() -> None:
    strategy = BaselineReversal(symbols=SYMBOLS, top_n=2, lookback=3)
    result = check_lookahead(strategy, provider(), SYMBOLS, times(range(10, 30)))
    assert result.stage is Stage.LOOKAHEAD
    assert result.passed, result.reason
    assert result.metrics["n_checked"] == 20
    assert result.metrics["n_mismatch"] == 0


@pytest.mark.parametrize("cheater", [PeekingStrategy(), FullHistoryNormalizer()])
def test_a_strategy_that_reads_the_future_fails(cheater: object) -> None:
    result = check_lookahead(cheater, provider(), SYMBOLS, times(range(10, 30)))  # type: ignore[arg-type]
    assert not result.passed
    assert result.metrics["n_mismatch"] > 0
    assert "differ" in result.reason


def test_a_strategy_that_asks_for_future_bars_fails() -> None:
    result = check_lookahead(AskingStrategy(), provider(), SYMBOLS, times(range(10, 12)))
    assert not result.passed
    assert "lookahead" in result.reason.lower()


def test_check_is_vacuous_without_future_data_and_must_fail() -> None:
    strategy = BaselineReversal(symbols=SYMBOLS, top_n=2, lookback=3)
    last = [T0 + timedelta(days=N_DAYS - 1)]
    result = check_lookahead(strategy, provider(), SYMBOLS, last)
    assert not result.passed
    assert "vacuous" in result.reason


def test_no_check_times_fails() -> None:
    strategy = BaselineReversal(symbols=SYMBOLS, top_n=2, lookback=3)
    assert not check_lookahead(strategy, provider(), SYMBOLS, []).passed


def test_truncated_provider_hides_everything_after_the_cutoff() -> None:
    cutoff = T0 + timedelta(days=5)
    cut: DataProvider = TruncatedProvider(provider(), cutoff)
    bars = cut.bars(SYMBOLS, T0 + timedelta(days=30))
    assert bars
    assert max(b.ts for b in bars) == cutoff
    assert cut.corporate_actions(SYMBOLS, T0 + timedelta(days=30)) == []
