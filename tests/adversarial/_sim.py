"""A tiny stand-in backtester so the adversaries can prove their own properties.

This is a measuring instrument for the adversary's self-tests, not the harness: no sizing, no
stops, flat per-side costs in basis points. Day `i` weights (decided at the close of day `i`
from a `DataView`) earn the close-to-close return into day `i + 1`.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

import numpy as np

from contracts.dataview import DataView
from contracts.strategy import Strategy
from tests.adversarial.strategies.noise import NoiseStrategy
from tests.adversarial.worlds import World

TRADING_DAYS = 252
WARMUP = 30


@dataclass(frozen=True)
class SimResult:
    gross: np.ndarray
    cost: np.ndarray
    entries: int

    @property
    def net(self) -> np.ndarray:
        net: np.ndarray = self.gross - self.cost
        return net

    @property
    def gross_mean(self) -> float:
        return float(self.gross.mean())

    @property
    def cost_mean(self) -> float:
        return float(self.cost.mean())

    @property
    def net_mean(self) -> float:
        return float(self.net.mean())

    def net_sum(self, lo: int, hi: int) -> float:
        """Net return summed over day indices [lo, hi) of the world."""
        a, b = max(lo - WARMUP, 0), max(hi - WARMUP, 0)
        return float(self.net[a:b].sum())

    @staticmethod
    def annual_sharpe(returns: np.ndarray) -> float:
        sd = float(returns.std())
        return 0.0 if sd == 0 else float(returns.mean() / sd * math.sqrt(TRADING_DAYS))

    @staticmethod
    def t_stat(returns: np.ndarray) -> float:
        sd = float(returns.std())
        return 0.0 if sd == 0 else float(returns.mean() / sd * math.sqrt(len(returns)))

    @property
    def profit_factor(self) -> float:
        net = self.net
        losses = float(-net[net < 0].sum())
        return math.inf if losses == 0 else float(net[net > 0].sum() / losses)


def simulate(
    world: World,
    strategy: Strategy,
    *,
    cost_bps: float = 0.0,
    start: int = WARMUP,
    end: int | None = None,
) -> SimResult:
    provider = world.provider()
    index = {s: j for j, s in enumerate(world.symbols)}
    last = len(world.timestamps) - 1 if end is None else min(end, len(world.timestamps) - 1)
    prev: dict[str, float] = {}
    gross: list[float] = []
    cost: list[float] = []
    entries = 0
    for i in range(start, last):
        asof = world.timestamps[i]
        weights = dict(strategy.generate(DataView(provider, asof), asof).weights)
        ret = sum(
            w * (world.closes[i + 1, index[s]] / world.closes[i, index[s]] - 1.0)
            for s, w in weights.items()
        )
        turnover = sum(
            abs(weights.get(s, 0.0) - prev.get(s, 0.0)) for s in set(weights) | set(prev)
        )
        entries += sum(1 for s, w in weights.items() if w > 0 and prev.get(s, 0.0) == 0)
        gross.append(float(ret))
        cost.append(turnover * cost_bps * 1e-4)
        prev = weights
    return SimResult(np.array(gross), np.array(cost), entries)


def signals_differ_under_truncation(
    strategy: Strategy, world: World, day_indices: Iterable[int]
) -> list[int]:
    """Days where the signal from a provider cut at t differs from the full provider's.

    This is the lookahead stage's definition of a leak, re-implemented here only to check the
    adversaries; the real stage lives in the harness.
    """
    full = world.provider()
    differing: list[int] = []
    for i in day_indices:
        asof = world.timestamps[i]
        a = strategy.generate(DataView(full, asof), asof).weights
        b = strategy.generate(DataView(world.provider(upto=asof), asof), asof).weights
        if dict(a) != dict(b):
            differing.append(i)
    return differing


def noise_sharpes(
    world: World, seeds: Iterable[int], *, start: int = WARMUP, end: int | None = None
) -> list[float]:
    """Gross annual Sharpe of `NoiseStrategy(seed)`, vectorised (its choice is a pure function)."""
    last = len(world.timestamps) - 1 if end is None else min(end, len(world.timestamps) - 1)
    simple = world.closes[1:] / world.closes[:-1] - 1.0
    days = range(start, last)
    out: list[float] = []
    for seed in seeds:
        picks = [world.symbols.index(NoiseStrategy.choice(world.symbols, seed, i)) for i in days]
        rets = np.array([simple[i, j] for i, j in zip(days, picks, strict=True)])
        out.append(SimResult.annual_sharpe(rets))
    return out


def best_of_n(world: World, n: int) -> tuple[int, float]:
    """Seed with the highest in-sample Sharpe among `n` noise strategies, and that Sharpe."""
    sharpes: Sequence[float] = noise_sharpes(world, range(n))
    best = int(np.argmax(sharpes))
    return best, float(sharpes[best])
