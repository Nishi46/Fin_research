"""Seeded synthetic worlds for the adversary suite. Not market data.

Return model: log return into day t is `e_t - rev_t * exponentially weighted mean
(span `window`) of earlier log returns`, with `e_t` iid Gaussian. `rev_t > 0` plants a
reversal edge, `rev_t < 0` a momentum (anti-reversal) tendency and `rev_t == 0` is pure
noise. An optional square-wave `cycle` adds a periodic drift.
Everything is deterministic given the seed.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

import numpy as np

from contracts.data import Bar
from fakes.data import InMemoryDataProvider
from harness.api import Regime
from harness.data.regimes import derive_regimes
from harness.thresholds import DEFAULT_THRESHOLDS

SIGNAL_WINDOW = 15  # EWMA span of the planted carry
DEFAULT_SYMBOLS = tuple(f"S{i:02d}" for i in range(12))
CLOSE_HOUR_UTC = 21

EDGE_SIGMA = 0.02  # daily vol of the edge worlds, a high-vol single-stock level
REGIME_START = date(2020, 3, 23)
REGIME_END = date(2024, 12, 31)


def weekdays(start: date, end: date) -> tuple[datetime, ...]:
    out: list[datetime] = []
    day = start
    while day <= end:
        if day.weekday() < 5:
            out.append(datetime(day.year, day.month, day.day, CLOSE_HOUR_UTC, tzinfo=UTC))
        day += timedelta(days=1)
    return tuple(out)


@dataclass(eq=False)
class World:
    name: str
    seed: int
    symbols: tuple[str, ...]
    timestamps: tuple[datetime, ...]
    closes: np.ndarray  # shape (days, symbols)
    bars: tuple[Bar, ...]
    reversion: tuple[float, ...]  # planted reversal strength per day (negative: momentum)
    window: int
    truth: Mapping[str, float]
    benchmark_bars: tuple[Bar, ...]
    regimes: tuple[Regime, ...]
    edge_regime: Regime | None = None

    def provider(self, upto: datetime | None = None) -> InMemoryDataProvider:
        """Serves the tradable symbols plus the benchmark (which is not in `symbols`)."""
        bars = [b for b in (*self.bars, *self.benchmark_bars) if upto is None or b.ts <= upto]
        return InMemoryDataProvider(bars, survivorship_biased=True)

    def day_range(self, regime: Regime) -> tuple[int, int]:
        """Half-open day-index range of `regime` inside this world ((0, 0) if absent)."""
        idx = [i for i, ts in enumerate(self.timestamps) if regime.contains(ts)]
        return (idx[0], idx[-1] + 1) if idx else (0, 0)


# Benchmark path: (advance fraction, selloff fraction); the rest is the recovery advance.
DEFAULT_BENCHMARK_PROFILE = (0.30, 0.15)
BENCHMARK_SELLOFF = 0.68  # selloff ends at 68% of the peak: a 32% fall, far from the 15% rule
BENCHMARK_NEW_RECORD = 0.15  # the recovery ends 15% above the old peak
BENCHMARK_ADVANCE_DRIFT = 0.0006
BENCHMARK_SIGMA = 0.004


def _benchmark(
    seed: int, timestamps: Sequence[datetime], profile: tuple[float, float]
) -> tuple[Bar, ...]:
    """A seeded benchmark: advance, then a deep fall, then a recovery to a new record."""
    days = len(timestamps)
    first, second = int(days * profile[0]), int(days * (profile[0] + profile[1]))
    fall = math.log(BENCHMARK_SELLOFF) / (second - first)
    recovery = (math.log(1.0 / BENCHMARK_SELLOFF) + BENCHMARK_NEW_RECORD) / (days - second)
    drift = np.array(
        [
            BENCHMARK_ADVANCE_DRIFT if t < first else fall if t < second else recovery
            for t in range(days)
        ]
    )
    rng = np.random.default_rng(seed + 10_000)
    lr = drift + rng.normal(0.0, BENCHMARK_SIGMA, size=days)
    lr[0] = 0.0
    closes = 100.0 * np.exp(np.cumsum(lr))
    bars = []
    for t, ts in enumerate(timestamps):
        close = float(closes[t])
        open_ = float(closes[t - 1]) if t else close
        bars.append(
            Bar(
                symbol=DEFAULT_THRESHOLDS.benchmark_symbol,
                ts=ts,
                open=open_,
                high=max(open_, close) * 1.002,
                low=min(open_, close) * 0.998,
                close=close,
                volume=1e8,
            )
        )
    return tuple(bars)


def _derive(benchmark: Sequence[Bar]) -> tuple[Regime, ...]:
    return derive_regimes(benchmark, min_drawdown=DEFAULT_THRESHOLDS.regime_min_drawdown)


def _build(
    name: str,
    seed: int,
    timestamps: tuple[datetime, ...],
    symbols: tuple[str, ...],
    reversion: np.ndarray | Callable[[tuple[Regime, ...]], np.ndarray],
    *,
    sigma: float,
    window: int,
    cycle_period: int = 0,
    cycle_amplitude: float = 0.0,
    truth: Mapping[str, float] | None = None,
    profile: tuple[float, float] = DEFAULT_BENCHMARK_PROFILE,
    edge_index: int | None = None,
) -> World:
    benchmark = _benchmark(seed, timestamps, profile)
    regimes = _derive(benchmark)
    if callable(reversion):
        reversion = reversion(regimes)
    rng = np.random.default_rng(seed)
    days, n = len(timestamps), len(symbols)
    noise = rng.normal(0.0, sigma, size=(days, n))
    lr = np.zeros((days, n))
    alpha = 2.0 / (window + 1)
    carry = np.zeros(n)
    for t in range(1, days):
        carry = alpha * lr[t - 1] + (1.0 - alpha) * carry
        drift = 0.0
        if cycle_period:
            drift = cycle_amplitude * (
                1.0 if math.cos(2 * math.pi * t / cycle_period) > 0 else -1.0
            )
        lr[t] = np.clip(noise[t] - reversion[t] * carry + drift, -0.2, 0.2)
    closes = 100.0 * np.exp(np.cumsum(lr, axis=0))
    bars: list[Bar] = []
    for j, symbol in enumerate(symbols):
        for t, ts in enumerate(timestamps):
            close = float(closes[t, j])
            open_ = float(closes[t - 1, j]) if t else close
            bars.append(
                Bar(
                    symbol=symbol,
                    ts=ts,
                    open=open_,
                    high=max(open_, close) * 1.002,
                    low=min(open_, close) * 0.998,
                    close=close,
                    volume=5_000_000.0,
                )
            )
    bars.sort(key=lambda b: (b.ts, b.symbol))
    return World(
        name=name,
        seed=seed,
        symbols=symbols,
        timestamps=timestamps,
        closes=closes,
        bars=tuple(bars),
        reversion=tuple(float(x) for x in reversion),
        window=window,
        truth=dict(truth or {}),
        benchmark_bars=benchmark,
        regimes=regimes,
        edge_regime=regimes[edge_index] if edge_index is not None else None,
    )


def planted_edge_world(
    seed: int,
    *,
    reversion: float = 0.95,
    sigma: float = EDGE_SIGMA,
    symbols: tuple[str, ...] = DEFAULT_SYMBOLS,
) -> World:
    """A real, strong, stationary reversal edge in every regime. The positive control."""
    ts = weekdays(REGIME_START, REGIME_END)
    rev = np.full(len(ts), reversion)
    return _build(
        "planted_edge",
        seed,
        ts,
        symbols,
        rev,
        sigma=sigma,
        window=SIGNAL_WINDOW,
        truth={"reversion": reversion},
    )


def noise_world(
    seed: int,
    *,
    days_start: date = date(2021, 1, 4),
    days_end: date = date(2024, 12, 31),
    symbols: tuple[str, ...] = DEFAULT_SYMBOLS,
) -> World:
    """Zero-drift iid returns. No strategy has an edge here."""
    ts = weekdays(days_start, days_end)
    return _build("noise", seed, ts, symbols, np.zeros(len(ts)), sigma=0.01, window=SIGNAL_WINDOW)


DEFAULT_ANTI_EDGE = 0.6
SINGLE_REGIME_START = date(2022, 7, 1)
SINGLE_REGIME_PROFILE = (0.12, 0.10)  # short advance and selloff, then the long final advance


def regime_world(
    seed: int,
    *,
    edge_index: int = -1,
    edge: float = 0.95,
    anti_edge: float = DEFAULT_ANTI_EDGE,
    symbols: tuple[str, ...] = DEFAULT_SYMBOLS,
) -> World:
    """Reversal edge only inside one derived regime (default: the final one).

    The regime is chosen by position among the regimes derived from the benchmark, never by
    name or date. Every other derived regime carries a momentum tendency.
    """
    ts = weekdays(SINGLE_REGIME_START, REGIME_END)

    def strength(regimes: tuple[Regime, ...]) -> np.ndarray:
        chosen = regimes[edge_index]
        return np.array([edge if chosen.contains(t) else -anti_edge for t in ts])

    return _build(
        "regime",
        seed,
        ts,
        symbols,
        strength,
        sigma=EDGE_SIGMA,
        window=SIGNAL_WINDOW,
        truth={"edge": edge},
        profile=SINGLE_REGIME_PROFILE,
        edge_index=edge_index,
    )


# Cost-blind world: low-vol, fast reversal, tuned against the real cost model (see test).
ASSUMED_COST_BPS_PER_SIDE = 5.0
COST_BLIND_REVERSION = 0.8
COST_BLIND_SIGMA = 0.0012
COST_BLIND_WINDOW = 10  # EWMA span; the strategy's lookback matches it


def cost_blind_world(seed: int, *, reversion: float = COST_BLIND_REVERSION) -> World:
    ts = weekdays(date(2021, 1, 4), date(2024, 12, 31))
    return _build(
        "cost_blind",
        seed,
        ts,
        DEFAULT_SYMBOLS,
        np.full(len(ts), reversion),
        sigma=COST_BLIND_SIGMA,
        window=COST_BLIND_WINDOW,
        truth={"reversion": reversion},
    )


# Param-peak world: a drift that flips sign with this exact period.
PEAK_PERIOD = 11


def param_peak_world(seed: int, *, amplitude: float = 0.0015) -> World:
    ts = weekdays(date(2021, 1, 4), date(2024, 12, 31))
    return _build(
        "param_peak",
        seed,
        ts,
        DEFAULT_SYMBOLS,
        np.zeros(len(ts)),
        sigma=0.01,
        window=SIGNAL_WINDOW,
        cycle_period=PEAK_PERIOD,
        cycle_amplitude=amplitude,
        truth={"period": float(PEAK_PERIOD), "amplitude": amplitude},
    )
