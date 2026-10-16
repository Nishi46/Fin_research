import ast
from collections.abc import Mapping
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from contracts import Bar, CapitalConfig, DataView, Intent, LookaheadError, MarketState, Order
from fakes.data import InMemoryDataProvider
from harness.api import Fill
from harness.costs.model import RealCostModel
from harness.engine.backtest import BacktestConfig, Backtester, SimulatedExecutor
from harness.engine.sizing import DefaultSizer
from harness.engine.stops import StopRule
from tests.helpers import T0


class ZeroCost:
    def cost(self, order: Order, capital: CapitalConfig, market: MarketState) -> float:
        return 0.0


class Scheduled:
    """Emits fixed weights on given day offsets (from T0), the previous weights otherwise."""

    strategy_id = "scheduled"

    def __init__(self, schedule: Mapping[int, Mapping[str, float]]) -> None:
        self._schedule = dict(schedule)
        self.seen_max_ts: list[datetime] = []

    def generate(self, data: DataView, asof: datetime) -> Intent:
        bars = data.bars(["AAA"])
        if bars:
            self.seen_max_ts.append(max(b.ts for b in bars))
        day = (asof - T0).days
        current: Mapping[str, float] = {}
        for d in sorted(self._schedule):
            if d <= day:
                current = self._schedule[d]
        return Intent(asof=asof, weights=current)


def bar(
    sym: str,
    day: int,
    o: float,
    c: float | None = None,
    *,
    h: float | None = None,
    lo: float | None = None,
    vol: float = 1e7,
) -> Bar:
    c = o if c is None else c
    return Bar(
        symbol=sym,
        ts=T0 + timedelta(days=day),
        open=o,
        high=h if h is not None else max(o, c),
        low=lo if lo is not None else min(o, c),
        close=c,
        volume=vol,
    )


def flat(sym: str, days: int, price: float = 100.0, vol: float = 1e7) -> list[Bar]:
    return [bar(sym, d, price, vol=vol) for d in range(days)]


def capital(amount: str = "10000") -> CapitalConfig:
    return CapitalConfig(amount=Decimal(amount), effective_at=T0, source="website")


def make(
    bars: list[Bar],
    strategy: Scheduled,
    *,
    cost: object | None = None,
    amount: str = "10000",
    config: BacktestConfig | None = None,
    executor: object | None = None,
    sizer: object | None = None,
) -> Backtester:
    return Backtester(
        provider=InMemoryDataProvider(bars),
        strategy=strategy,
        capital=capital(amount),
        cost_model=cost or ZeroCost(),  # type: ignore[arg-type]
        sizer=sizer or DefaultSizer(),  # type: ignore[arg-type]
        executor=executor or SimulatedExecutor(),  # type: ignore[arg-type]
        config=config or BacktestConfig(),
    )


def run(bt: Backtester, days: int, symbols: tuple[str, ...] = ("AAA",)):  # type: ignore[no-untyped-def]
    return bt.run(symbols, T0, T0 + timedelta(days=days - 1))


def test_signal_at_close_executes_at_the_next_bars_open() -> None:
    bars = [
        bar("AAA", 0, 100),
        bar("AAA", 1, 120, 121),
        bar("AAA", 2, 121, 122),
        bar("AAA", 3, 122),
    ]
    out = run(make(bars, Scheduled({0: {"AAA": 1.0}})), 4)
    (trade,) = out.trades
    assert trade.entry_price == 120.0  # next bar's open, not day 0's close of 100
    assert trade.entry_ts == T0 + timedelta(days=1)


def test_buy_is_limited_to_the_cash_available() -> None:
    bars = [bar("AAA", 0, 100), bar("AAA", 1, 120), bar("AAA", 2, 120)]
    out = run(make(bars, Scheduled({0: {"AAA": 1.0}})), 3)
    (trade,) = out.trades
    assert trade.shares * trade.entry_price <= 10_000.0 + 1e-6
    assert trade.shares == 83.0  # 100 sized at the 100 close, only 83 affordable at 120


def test_the_last_bars_signal_is_never_traded() -> None:
    out = run(make(flat("AAA", 3), Scheduled({2: {"AAA": 1.0}})), 3)
    assert out.trades == ()


def test_strategy_only_sees_data_up_to_the_decision_time() -> None:
    strat = Scheduled({0: {"AAA": 1.0}})
    run(make(flat("AAA", 6), strat), 6)
    # one call per decision day 0..4 (day 5 has no next bar), each seeing exactly up to that day
    assert strat.seen_max_ts == [T0 + timedelta(days=d) for d in range(5)]


def test_strategy_cannot_read_the_future_through_the_view() -> None:
    class Peeker:
        strategy_id = "peek"

        def generate(self, data: DataView, asof: datetime) -> Intent:
            data.bars(["AAA"], asof + timedelta(days=1))
            return Intent(asof=asof, weights={})

    bt = make(flat("AAA", 4), Peeker())  # type: ignore[arg-type]
    with pytest.raises(LookaheadError):
        run(bt, 4)


def test_intent_asof_must_match_the_decision_time() -> None:
    class Wrong:
        strategy_id = "wrong"

        def generate(self, data: DataView, asof: datetime) -> Intent:
            return Intent(asof=asof - timedelta(days=1), weights={})

    with pytest.raises(ValueError, match="asof"):
        run(make(flat("AAA", 3), Wrong()), 3)  # type: ignore[arg-type]


def test_intent_for_a_symbol_outside_the_universe_is_refused() -> None:
    with pytest.raises(ValueError, match="universe"):
        run(make(flat("AAA", 3), Scheduled({0: {"ZZZ": 0.5}})), 3)


def test_returns_follow_the_price_with_zero_costs() -> None:
    bars = [bar("AAA", 0, 100), bar("AAA", 1, 100, 110), bar("AAA", 2, 110, 99), bar("AAA", 3, 99)]
    out = run(make(bars, Scheduled({0: {"AAA": 1.0}})), 4)
    # 100 shares bought at 100; marked 11000 then 9900; liquidated at day 3's close (99)
    assert out.daily_returns[0] == 0.0
    assert out.daily_returns[1] == pytest.approx(0.10)
    assert out.daily_returns[2] == pytest.approx(9900 / 11000 - 1)
    assert out.daily_returns[3] == pytest.approx(0.0)
    (trade,) = out.trades
    assert trade.pnl == pytest.approx(-100.0)


def test_costs_reduce_pnl_and_are_recorded_on_the_trade() -> None:
    bars = flat("AAA", 5)
    free = run(make(bars, Scheduled({0: {"AAA": 0.5}})), 5)
    paid = run(make(bars, Scheduled({0: {"AAA": 0.5}}), cost=RealCostModel()), 5)
    assert free.trades[0].cost == 0.0
    assert paid.trades[0].cost > 0.0
    assert paid.trades[0].pnl == pytest.approx(free.trades[0].pnl - paid.trades[0].cost)
    assert paid.total_cost == pytest.approx(paid.trades[0].cost)
    assert sum(paid.daily_returns) < sum(free.daily_returns)


def test_pnl_and_returns_agree() -> None:
    bars = [
        bar("AAA", 0, 100),
        bar("AAA", 1, 100, 104),
        bar("AAA", 2, 104, 108),
        bar("AAA", 3, 108),
    ]
    out = run(make(bars, Scheduled({0: {"AAA": 1.0}}), cost=RealCostModel()), 4)
    growth = 1.0
    for r in out.daily_returns:
        growth *= 1.0 + r
    assert 10_000 * growth - 10_000 == pytest.approx(sum(t.pnl for t in out.trades), rel=1e-6)


def test_gap_through_a_stop_fills_at_the_open() -> None:
    bars = [
        bar("AAA", 0, 100),
        bar("AAA", 1, 100),
        bar("AAA", 2, 80, 82, h=83, lo=78),  # gaps below the 95 stop
        bar("AAA", 3, 82),
    ]
    cfg = BacktestConfig(stop_rule=StopRule(stop_loss_frac=0.05))
    out = run(make(bars, Scheduled({0: {"AAA": 1.0}}), config=cfg), 4)
    trade = out.trades[0]  # the strategy still wants AAA, so it re-enters later; see next test
    assert trade.exit_price == 80.0
    assert trade.exit_ts == T0 + timedelta(days=2)
    assert trade.pnl == pytest.approx(-2000.0)


def test_intrabar_stop_fills_at_the_stop_price() -> None:
    bars = [
        bar("AAA", 0, 100),
        bar("AAA", 1, 100),
        bar("AAA", 2, 99, 96, h=100, lo=90),
        bar("AAA", 3, 96),
    ]
    cfg = BacktestConfig(stop_rule=StopRule(stop_loss_frac=0.05))
    out = run(make(bars, Scheduled({0: {"AAA": 1.0}}), config=cfg), 4)
    assert out.trades[0].exit_price == pytest.approx(95.0)


def test_stopped_out_symbol_is_not_rebought_on_the_same_bar() -> None:
    bars = [bar("AAA", 0, 100), bar("AAA", 1, 100), bar("AAA", 2, 80), bar("AAA", 3, 80)]
    cfg = BacktestConfig(stop_rule=StopRule(stop_loss_frac=0.05))
    out = run(make(bars, Scheduled({0: {"AAA": 1.0}}), config=cfg), 4)
    stop_trade, reentry = out.trades
    assert stop_trade.exit_ts == T0 + timedelta(days=2)
    # a fresh signal at the day 2 close may re-enter on day 3, never on the stop bar itself
    assert reentry.entry_ts == T0 + timedelta(days=3)


def test_same_signals_at_different_capital_give_different_positions_and_costs() -> None:
    syms = {"HIGH": 400.0, "MID": 150.0, "LOW": 20.0}
    bars = [b for s, p in syms.items() for b in flat(s, 6, p)]
    weights = {"HIGH": 0.05, "MID": 0.05, "LOW": 0.9}
    small = make(bars, Scheduled({0: weights}), cost=RealCostModel(), amount="2000")
    large = make(bars, Scheduled({0: weights}), cost=RealCostModel(), amount="200000")
    names = tuple(syms)
    out_small = small.run(names, T0, T0 + timedelta(days=5))
    out_large = large.run(names, T0, T0 + timedelta(days=5))
    assert len(out_small.trades) == 1
    assert len(out_large.trades) == 3
    assert out_large.total_cost > out_small.total_cost * 50


def test_thin_market_limits_the_position_to_the_liquidity_cap() -> None:
    bars = flat("AAA", 5, 100.0, vol=1000.0)  # 100k daily dollar volume, 5% cap = 5k = 50 shares
    out = run(make(bars, Scheduled({0: {"AAA": 1.0}}), cost=RealCostModel()), 5)
    assert out.trades[0].shares == pytest.approx(50.0)


def test_no_volume_means_no_trade_and_it_is_counted() -> None:
    out = run(make(flat("AAA", 5, vol=0.0), Scheduled({0: {"AAA": 1.0}})), 5)
    assert out.trades == ()
    assert out.skipped_orders > 0


def test_holding_across_an_event_is_flagged() -> None:
    event = T0 + timedelta(days=3)
    cfg = BacktestConfig(event_dates={"AAA": (event,)})
    out = run(make(flat("AAA", 6), Scheduled({0: {"AAA": 1.0}}), config=cfg), 6)
    assert out.earnings_hold_flags == (True,)


def test_hold_that_misses_the_event_is_not_flagged() -> None:
    cfg = BacktestConfig(event_dates={"AAA": (T0 + timedelta(days=9),)})
    out = run(make(flat("AAA", 6), Scheduled({0: {"AAA": 1.0}}), config=cfg), 6)
    assert out.earnings_hold_flags == (False,)


def test_injected_executor_decides_the_fill_price() -> None:
    class Slippy:
        def execute(self, order: Order, market: MarketState) -> Fill:
            price = market.price * (1.01 if order.side == "buy" else 0.99)
            return Fill(
                symbol=order.symbol,
                side=order.side,
                shares=order.shares,
                price=price,
                ts=market.asof,
            )

    out = run(make(flat("AAA", 5), Scheduled({0: {"AAA": 0.5}}), executor=Slippy()), 5)
    assert out.trades[0].entry_price == pytest.approx(101.0)
    assert out.trades[0].exit_price == pytest.approx(99.0)


def test_rebalance_band_stops_tiny_trades() -> None:
    # price drifts so the held position stays within the band of its target: no extra trades
    bars = [bar("AAA", d, 100 + 0.1 * d) for d in range(8)]
    out = run(make(bars, Scheduled({0: {"AAA": 0.5}}), cost=RealCostModel()), 8)
    assert len(out.trades) == 1


def test_exit_when_the_target_goes_to_zero() -> None:
    sched = {0: {"AAA": 1.0}, 3: {}}
    out = run(make(flat("AAA", 8), Scheduled(sched)), 8)
    (trade,) = out.trades
    assert trade.exit_ts == T0 + timedelta(days=4)


def test_runs_are_deterministic() -> None:
    bars = [bar("AAA", d, 100 + (d % 3)) for d in range(10)]
    a = run(make(bars, Scheduled({0: {"AAA": 0.7}}), cost=RealCostModel()), 10)
    b = run(make(bars, Scheduled({0: {"AAA": 0.7}}), cost=RealCostModel()), 10)
    assert a == b


def test_timestamps_align_with_returns() -> None:
    out = run(make(flat("AAA", 7), Scheduled({})), 7)
    assert len(out.timestamps) == len(out.daily_returns) == 7
    assert out.daily_returns == (0.0,) * 7


def test_backtester_modules_do_not_import_broker_or_live_code() -> None:
    root = Path(__file__).resolve().parents[3] / "harness" / "engine"
    banned = {"engine", "broker", "risk", "strategies", "proposer"}
    for path in root.glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            for name in names:
                assert name.split(".")[0] not in banned, f"{path.name} imports {name}"


def test_buy_that_cannot_afford_one_whole_share_is_skipped_not_a_crash() -> None:
    # Sized at the 100 close: 1 share fits 100.5. At the 100.49 open, share plus cost is just
    # over the cash, so the scaled-down order falls under one whole share.
    bars = [bar("AAA", 0, 100), bar("AAA", 1, 100.49), bar("AAA", 2, 100.49), bar("AAA", 3, 100.49)]
    bt = make(bars, Scheduled({0: {"AAA": 1.0}}), cost=RealCostModel(), amount="100.5")
    out = run(bt, 4)
    assert out.trades == ()
    assert out.skipped_orders > 0
    assert out.daily_returns == (0.0,) * 4


def test_partial_liquidity_exit_of_a_stopped_position_completes() -> None:
    # The exit is clipped by the liquidity cap to a fraction of a share's worth; must not crash.
    bars = [
        bar("AAA", 0, 100, vol=1e6),
        bar("AAA", 1, 100, vol=1e6),
        bar("AAA", 2, 80, vol=1.0),
        bar("AAA", 3, 80, vol=1.0),
        bar("AAA", 4, 80, vol=1.0),
    ]
    cfg = BacktestConfig(stop_rule=StopRule(stop_loss_frac=0.05))
    out = run(make(bars, Scheduled({0: {"AAA": 1.0}}), cost=RealCostModel(), config=cfg), 5)
    assert len(out.timestamps) == 5


@pytest.mark.parametrize("seed", range(40))
def test_random_small_accounts_never_build_a_zero_share_order(seed: int) -> None:
    import random

    rng = random.Random(seed)
    price = rng.uniform(5, 500)
    bars = []
    prev = price
    for d in range(12):
        o = prev * rng.uniform(0.9, 1.1)
        c = o * rng.uniform(0.95, 1.05)
        bars.append(bar("AAA", d, o, c, vol=rng.choice([1.0, 50.0, 1e5, 1e7])))
        prev = c
    sched = {d: {"AAA": rng.choice([0.0, 0.3, 1.0])} for d in range(0, 12, 2)}
    cfg = BacktestConfig(stop_rule=StopRule(stop_loss_frac=0.03, take_profit_frac=0.04))
    amount = str(rng.choice([price * 1.001, price * 1.5, 50.0, 1000.0, 100000.0]))
    out = run(make(bars, Scheduled(sched), cost=RealCostModel(), amount=amount, config=cfg), 12)
    assert all(t.shares > 0 for t in out.trades)
