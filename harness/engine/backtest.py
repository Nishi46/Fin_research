"""Event-driven daily backtester.

One loop, one code path: a signal generated at the close of bar t is sized at that close and
executes at the open of bar t+1. The strategy only ever sees a `DataView` limited to t. Cost
model, sizer and executor are injected, so the live engine can later supply a broker-backed
executor and the real risk-layer sizer without changing this loop. This module imports no
broker, risk or live-engine code.

Accounting: cash plus marked positions. Weights apply to current equity (the account compounds),
passed to the sizer as a `CapitalConfig`. Every fill pays cost from the injected cost model.
Open positions are liquidated at the final close, with costs, so trades are all closed.
"""

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from contracts import (
    Bar,
    CapitalConfig,
    CostModel,
    DataProvider,
    DataView,
    MarketState,
    Order,
    Strategy,
)
from harness.api import Executor, Fill, Sizer, TradeRecord
from harness.engine.stops import StopRule, crosses_event, long_exit_fill
from harness.thresholds import DEFAULT_THRESHOLDS

EPS = 1e-9
AFFORDABILITY_SAFETY = 0.9999
AFFORDABILITY_PASSES = 6


@dataclass(frozen=True)
class BacktestConfig:
    stop_rule: StopRule | None = None
    adv_window: int = 20
    # Trade an existing position only if the change is at least this fraction of equity.
    # Entries and full exits always trade.
    rebalance_band: float = 0.02
    max_adv_participation: float = DEFAULT_THRESHOLDS.max_adv_participation
    # Per-symbol event timestamps (for example earnings). Trades held across one are flagged.
    event_dates: Mapping[str, tuple[datetime, ...]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.adv_window < 1:
            raise ValueError("adv_window must be at least 1")
        if self.rebalance_band < 0:
            raise ValueError("rebalance_band must be non-negative")
        if not 0.0 < self.max_adv_participation <= 1.0:
            raise ValueError("max_adv_participation must be in (0, 1]")


@dataclass(frozen=True)
class SimulationOutput:
    timestamps: tuple[datetime, ...]
    daily_returns: tuple[float, ...]
    trades: tuple[TradeRecord, ...]
    # Aligned with `trades`: held across a configured event, so stop fills are unreliable.
    earnings_hold_flags: tuple[bool, ...]
    skipped_orders: int
    total_cost: float


class SimulatedExecutor:
    """Fills at the reference price in the market state. Costs are charged separately."""

    def execute(self, order: Order, market: MarketState) -> Fill:
        return Fill(
            symbol=order.symbol,
            side=order.side,
            shares=order.shares,
            price=market.price,
            ts=market.asof,
        )


@dataclass
class _Position:
    shares: float
    avg_entry: float
    entry_ts: datetime
    bought_shares: float = 0.0
    buy_notional: float = 0.0
    sold_shares: float = 0.0
    sell_notional: float = 0.0
    cost: float = 0.0
    bought_this_bar: bool = False


class Backtester:
    def __init__(
        self,
        provider: DataProvider,
        strategy: Strategy,
        capital: CapitalConfig,
        cost_model: CostModel,
        sizer: Sizer,
        executor: Executor,
        config: BacktestConfig | None = None,
    ) -> None:
        self._provider = provider
        self._strategy = strategy
        self._capital = capital
        self._cost_model = cost_model
        self._sizer = sizer
        self._executor = executor
        self._config = config or BacktestConfig()

    def run(self, symbols: Sequence[str], start: datetime, end: datetime) -> SimulationOutput:
        universe = tuple(symbols)
        series = self._load(universe, end)
        dates = sorted({b.ts for bars in series.values() for b in bars if start <= b.ts <= end})
        sim = _Simulation(self, universe, series)
        for i, ts in enumerate(dates):
            sim.step(ts, is_last=i == len(dates) - 1)
        return sim.output()

    def _load(self, universe: tuple[str, ...], end: datetime) -> dict[str, list[Bar]]:
        series: dict[str, list[Bar]] = {s: [] for s in universe}
        for b in self._provider.bars(universe, end):
            series[b.symbol].append(b)
        for bars in series.values():
            bars.sort(key=lambda b: b.ts)
        return series


class _Simulation:
    def __init__(
        self, bt: Backtester, universe: tuple[str, ...], series: dict[str, list[Bar]]
    ) -> None:
        self._bt = bt
        self._cfg = bt._config
        self._universe = universe
        self._series = series
        self._index = {s: {b.ts: k for k, b in enumerate(bars)} for s, bars in series.items()}
        self._adv = {s: self._rolling_adv(bars) for s, bars in series.items()}
        self._cash = float(bt._capital.amount)
        self._positions: dict[str, _Position] = {}
        self._last_close: dict[str, float] = {}
        self._prev_equity = self._cash
        self._pending: Mapping[str, float] | None = None
        self._pending_adv: dict[str, float] = {}
        self._pending_capital: CapitalConfig = bt._capital
        self._timestamps: list[datetime] = []
        self._returns: list[float] = []
        self._trades: list[TradeRecord] = []
        self._skipped = 0
        self._total_cost = 0.0

    def _rolling_adv(self, bars: list[Bar]) -> list[float]:
        window = self._cfg.adv_window
        dollars = [b.close * b.volume for b in bars]
        return [
            sum(dollars[max(0, k - window + 1) : k + 1])
            / len(dollars[max(0, k - window + 1) : k + 1])
            for k in range(len(bars))
        ]

    # ---- one bar ----------------------------------------------------------------------

    def step(self, ts: datetime, *, is_last: bool) -> None:
        today = {
            s: self._series[s][k]
            for s in self._universe
            if (k := self._index[s].get(ts)) is not None
        }
        for position in self._positions.values():
            position.bought_this_bar = False
        stopped = self._run_stops(ts, today, only_new=False)
        if self._pending is not None:
            self._rebalance(ts, today, stopped)
            self._pending = None
            self._run_stops(ts, today, only_new=True)
        for s, b in today.items():
            self._last_close[s] = b.close
        if is_last:
            self._liquidate(ts, today)
        equity = self._equity()
        self._timestamps.append(ts)
        self._returns.append(equity / self._prev_equity - 1.0)
        self._prev_equity = equity
        if not is_last and equity > 0:
            self._signal(ts, today, equity)

    def _equity(self) -> float:
        return self._cash + sum(p.shares * self._last_close[s] for s, p in self._positions.items())

    # ---- signal -----------------------------------------------------------------------

    def _signal(self, ts: datetime, today: Mapping[str, Bar], equity: float) -> None:
        bt = self._bt
        intent = bt._strategy.generate(DataView(bt._provider, ts), ts)
        if intent.asof != ts:
            raise ValueError(f"intent asof {intent.asof} does not match decision time {ts}")
        outside = set(intent.weights) - set(self._universe)
        if outside:
            raise ValueError(f"intent names symbols outside the universe: {sorted(outside)}")
        capital = CapitalConfig(
            amount=Decimal(repr(equity)),
            currency=bt._capital.currency,
            effective_at=ts,
            source=bt._capital.source,
        )
        market: dict[str, MarketState] = {}
        advs: dict[str, float] = {}
        for s in intent.weights:
            if s not in today and s not in self._last_close:
                continue
            k = self._latest_index(s, ts)
            advs[s] = self._adv[s][k]
            market[s] = MarketState(
                symbol=s, asof=ts, price=self._last_close[s], avg_daily_dollar_volume=advs[s]
            )
        self._pending = bt._sizer.target_shares(intent, capital, market)
        # A wanted position the sizer cannot hold (no volume, below one share) is not traded.
        self._skipped += sum(
            1 for s, w in intent.weights.items() if w > 0 and self._pending.get(s, 0.0) <= 0
        )
        self._pending_adv = advs
        self._pending_capital = capital

    def _latest_index(self, symbol: str, ts: datetime) -> int:
        k = self._index[symbol].get(ts)
        if k is not None:
            return k
        return max(i for t, i in self._index[symbol].items() if t < ts)

    # ---- trading ----------------------------------------------------------------------

    def _rebalance(self, ts: datetime, today: Mapping[str, Bar], stopped: set[str]) -> None:
        assert self._pending is not None
        targets = self._pending
        orders: list[tuple[str, float]] = []
        for s in sorted(set(targets) | set(self._positions)):
            if s in stopped or s not in today:
                continue
            held = self._positions[s].shares if s in self._positions else 0.0
            diff = targets.get(s, 0.0) - held
            price = today[s].open
            if abs(diff) < EPS:
                continue
            is_entry_or_exit = held == 0.0 or targets.get(s, 0.0) == 0.0
            if (
                not is_entry_or_exit
                and abs(diff) * price < self._cfg.rebalance_band * self._prev_equity
            ):
                continue
            orders.append((s, diff))
        for s, diff in sorted(orders, key=lambda o: o[1] > 0):  # sells first free the cash
            self._trade(s, diff, today[s].open, ts)

    def _market(self, symbol: str, price: float, ts: datetime) -> MarketState | None:
        adv = self._pending_adv.get(symbol)
        if adv is None:
            adv = self._adv[symbol][self._latest_index(symbol, ts)]
        if adv <= 0:
            return None
        return MarketState(symbol=symbol, asof=ts, price=price, avg_daily_dollar_volume=adv)

    def _trade(self, symbol: str, diff: float, price: float, ts: datetime) -> None:
        market = self._market(symbol, price, ts)
        if market is None:
            self._skipped += 1
            return
        shares = abs(diff)
        shares = min(
            shares, self._cfg.max_adv_participation * market.avg_daily_dollar_volume / price
        )
        side = "buy" if diff > 0 else "sell"
        if side == "buy":
            shares = self._affordable(symbol, shares, market)
        if shares < EPS:
            self._skipped += 1
            return
        order = Order(symbol=symbol, side=side, shares=shares)
        fill = self._bt._executor.execute(order, market)
        cost = self._bt._cost_model.cost(
            Order(symbol=symbol, side=side, shares=fill.shares),
            self._pending_capital,
            market.model_copy(update={"price": fill.price}),
        )
        self._apply(fill, cost, ts)

    def _affordable(self, symbol: str, shares: float, market: MarketState) -> float:
        whole = abs(shares - round(shares)) < EPS  # keep whole-share sizing whole
        for _ in range(AFFORDABILITY_PASSES):
            shares = float(math.floor(shares)) if whole else shares
            if shares < EPS:  # cannot afford even one whole share: nothing to order
                return 0.0
            order = Order(symbol=symbol, side="buy", shares=shares)
            cost = self._bt._cost_model.cost(order, self._pending_capital, market)
            total = shares * market.price + cost
            if total <= self._cash:
                return shares
            shares *= self._cash / total * AFFORDABILITY_SAFETY
        return 0.0

    def _apply(self, fill: Fill, cost: float, ts: datetime) -> None:
        self._total_cost += cost
        notional = fill.shares * fill.price
        if fill.side == "buy":
            self._cash -= notional + cost
            p = self._positions.get(fill.symbol)
            if p is None:
                p = _Position(shares=0.0, avg_entry=fill.price, entry_ts=ts)
                self._positions[fill.symbol] = p
            total = p.shares + fill.shares
            p.avg_entry = (p.avg_entry * p.shares + fill.price * fill.shares) / total
            p.shares = total
            p.bought_shares += fill.shares
            p.buy_notional += notional
            p.cost += cost
            p.bought_this_bar = True
            return
        p = self._positions[fill.symbol]
        self._cash += notional - cost
        p.shares -= fill.shares
        p.sold_shares += fill.shares
        p.sell_notional += notional
        p.cost += cost
        if p.shares < EPS:
            self._close(fill.symbol, p, ts)

    def _close(self, symbol: str, p: _Position, ts: datetime) -> None:
        self._trades.append(
            TradeRecord(
                symbol=symbol,
                entry_ts=p.entry_ts,
                exit_ts=ts,
                entry_price=p.buy_notional / p.bought_shares,
                exit_price=p.sell_notional / p.sold_shares,
                shares=p.bought_shares,
                cost=p.cost,
                pnl=p.sell_notional - p.buy_notional - p.cost,
            )
        )
        del self._positions[symbol]

    # ---- stops and liquidation ----------------------------------------------------------

    def _run_stops(self, ts: datetime, today: Mapping[str, Bar], *, only_new: bool) -> set[str]:
        rule = self._cfg.stop_rule
        stopped: set[str] = set()
        if rule is None:
            return stopped
        for s in sorted(self._positions):
            p = self._positions.get(s)
            b = today.get(s)
            if p is None or b is None or (only_new and not p.bought_this_bar):
                continue
            exit_fill = long_exit_fill(p.avg_entry, rule, open_=b.open, high=b.high, low=b.low)
            if exit_fill is None:
                continue
            self._trade(s, -p.shares, exit_fill.price, ts)
            if s not in self._positions:
                stopped.add(s)
        return stopped

    def _liquidate(self, ts: datetime, today: Mapping[str, Bar]) -> None:
        for s in sorted(self._positions):
            p = self._positions.get(s)
            if p is not None and s in today:
                self._trade(s, -p.shares, today[s].close, ts)

    # ---- result -----------------------------------------------------------------------

    def output(self) -> SimulationOutput:
        events = self._cfg.event_dates
        flags = tuple(
            crosses_event(t.entry_ts, t.exit_ts, events.get(t.symbol, ())) for t in self._trades
        )
        return SimulationOutput(
            timestamps=tuple(self._timestamps),
            daily_returns=tuple(self._returns),
            trades=tuple(self._trades),
            earnings_hold_flags=flags,
            skipped_orders=self._skipped,
            total_cost=self._total_cost,
        )
