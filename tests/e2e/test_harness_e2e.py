"""One candidate through the real harness: data layer, backtester, pipeline, registry.

Nothing here is mocked. The bars are synthetic (a seeded random walk, no edge) on real NYSE
sessions, with a recorded 2-for-1 split so the adjustment path is exercised end to end.
"""

from datetime import UTC, date, datetime
from decimal import Decimal

import numpy as np
import pytest

from contracts.capital import CapitalConfig
from contracts.data import Bar, CorporateAction, CorporateActionKind
from contracts.dataview import DataView
from contracts.intent import Intent
from contracts.registry import Outcome
from harness.data.calendar import trading_sessions
from harness.data.provider import ComposedDataProvider
from harness.engine.runner import run_backtest
from harness.registry.sqlite import SqliteRegistry
from harness.validation.pipeline import PipelineResult, run_pipeline
from strategies.baseline import BaselineReversal
from tests.data_fixtures import series

SEED = 11
START = date(2024, 1, 2)
N_SESSIONS = 260
SPLIT_INDEX = 120
SYMBOLS = ["AAA", "BBB", "CCC", "SPL"]
LOOKBACK = 5


class EqualWeightHold:
    """Always fully invested, equal weight. Used to put the split symbol in the book."""

    strategy_id = "equal_weight_hold"

    def __init__(self, symbols: list[str]) -> None:
        self._symbols = symbols

    def generate(self, data: DataView, asof: datetime) -> Intent:
        present = sorted({b.symbol for b in data.bars(self._symbols)})
        weight = 1.0 / len(present) if present else 0.0
        return Intent(asof=asof, weights={s: weight for s in present})


def _bars() -> list[Bar]:
    rng = np.random.default_rng(SEED)
    out: list[Bar] = []
    for symbol in SYMBOLS:
        walk = 100.0 * np.exp(np.cumsum(0.01 * rng.standard_normal(N_SESSIONS)))
        closes = [float(c) for c in walk]
        if symbol == "SPL":
            # Raw (unadjusted) prices halve at the split, as a vendor's raw feed would show.
            closes = [c if i < SPLIT_INDEX else c / 2.0 for i, c in enumerate(closes)]
        out.extend(series(symbol, closes, start=START))
    return out


def _split_action() -> CorporateAction:
    session = trading_sessions(START, date(2025, 12, 31))[SPLIT_INDEX]
    return CorporateAction(
        symbol="SPL",
        kind=CorporateActionKind.SPLIT,
        effective_at=datetime(session.year, session.month, session.day, tzinfo=UTC),
        value=2.0,
    )


def _provider(*, with_split_action: bool = True) -> ComposedDataProvider:
    actions = [_split_action()] if with_split_action else []
    return ComposedDataProvider(_bars(), corporate_actions=actions)


def _capital(amount: str = "10000") -> CapitalConfig:
    first = min(b.ts for b in _bars())
    return CapitalConfig(amount=Decimal(amount), effective_at=first, source="default")


def _window() -> tuple[datetime, datetime]:
    stamps = sorted({b.ts for b in _bars()})
    return stamps[0], stamps[-1]


def _run_pipeline(registry: SqliteRegistry) -> PipelineResult:
    start, end = _window()
    return run_pipeline(
        strategy=BaselineReversal(symbols=SYMBOLS, top_n=2, lookback=LOOKBACK),
        build=lambda p: BaselineReversal(symbols=SYMBOLS, top_n=2, lookback=int(p["lookback"])),
        params={"lookback": LOOKBACK},
        provider=_provider(),
        symbols=SYMBOLS,
        start=start,
        end=end,
        capital=_capital(),
        registry=registry,
        seed=SEED,
    )


def test_a_candidate_flows_through_data_backtest_pipeline_and_registry() -> None:
    registry = SqliteRegistry()

    result = _run_pipeline(registry)

    assert registry.count() == 1
    stored = registry.get(result.trial.id)
    assert stored == result.trial
    assert result.stages, "at least one stage must have run"
    assert result.stage_reached == result.stages[-1].stage
    assert stored.stage_reached == result.stage_reached
    assert stored.outcome == result.outcome
    assert stored.outcome in {Outcome.REJECTED, Outcome.PASSED}, "no ERROR on a healthy run"
    assert "sharpe_per_period" in stored.metrics
    assert stored.data_snapshot_id == _provider().snapshot().id
    assert stored.survivorship_biased is True, "free data is always survivorship-biased"
    assert stored.capital_amount == Decimal("10000")


def test_a_random_walk_has_no_edge_so_it_is_not_passed() -> None:
    result = _run_pipeline(SqliteRegistry())

    assert result.outcome == Outcome.REJECTED


def test_same_seed_and_snapshot_reproduce_the_same_result() -> None:
    first = _run_pipeline(SqliteRegistry())
    second = _run_pipeline(SqliteRegistry())

    assert first.trial.id != second.trial.id, "each run is its own trial"
    assert first.outcome == second.outcome
    assert first.stage_reached == second.stage_reached
    assert [(s.stage, s.passed, s.reason, dict(s.metrics)) for s in first.stages] == [
        (s.stage, s.passed, s.reason, dict(s.metrics)) for s in second.stages
    ]
    assert dict(first.trial.metrics) == dict(second.trial.metrics)
    assert first.trial.data_snapshot_id == second.trial.data_snapshot_id
    assert first.trial.code_hash == second.trial.code_hash


def _hold(provider: ComposedDataProvider, capital: CapitalConfig) -> tuple[float, float]:
    start, end = _window()
    result = run_backtest(
        provider=provider,
        strategy=EqualWeightHold(SYMBOLS),
        symbols=SYMBOLS,
        start=start,
        end=end,
        capital=capital,
        seed=SEED,
        code_version="test",
    )
    return min(result.daily_returns), sum(t.cost for t in result.trades)


def test_a_two_for_one_split_does_not_look_like_a_crash() -> None:
    worst_adjusted, _ = _hold(_provider(with_split_action=True), _capital())
    worst_raw, _ = _hold(_provider(with_split_action=False), _capital())

    # One of four equal-weight names halves: unadjusted that is about -12.5% in a day.
    assert worst_raw < -0.10
    assert worst_adjusted > -0.10


@pytest.mark.parametrize(("small", "large"), [("2000", "200000")])
def test_larger_capital_costs_more_for_the_same_signals(small: str, large: str) -> None:
    _, cost_small = _hold(_provider(), _capital(small))
    _, cost_large = _hold(_provider(), _capital(large))

    assert cost_large > cost_small
