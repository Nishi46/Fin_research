"""Returns beyond market exposure: regress strategy returns on SPY returns."""

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

from harness.metrics.core import TRADING_DAYS_PER_YEAR

MIN_OBSERVATIONS = 2


@dataclass(frozen=True)
class BenchmarkStats:
    beta: float
    alpha_daily: float
    alpha_annual: float
    r_squared: float
    # strategy return minus beta * benchmark return, per day (alpha plus noise)
    residual_returns: tuple[float, ...]


def beta_alpha(
    strategy: Sequence[float],
    benchmark: Sequence[float],
    periods_per_year: int = TRADING_DAYS_PER_YEAR,
) -> BenchmarkStats:
    """OLS of strategy on benchmark. Alpha is the intercept: what is left once market exposure
    is removed. Both series must be the same trading days, in the same order."""
    if len(strategy) != len(benchmark):
        raise ValueError("strategy and benchmark returns must align one-to-one")
    if len(strategy) < MIN_OBSERVATIONS:
        raise ValueError(f"need at least {MIN_OBSERVATIONS} observations")
    y = np.asarray(strategy, dtype=float)
    x = np.asarray(benchmark, dtype=float)
    if not (np.all(np.isfinite(x)) and np.all(np.isfinite(y))):
        raise ValueError("returns must be finite")
    x_centered = x - x.mean()
    sxx = float(x_centered @ x_centered)
    if sxx <= 1e-18:
        raise ValueError("benchmark has no variance, beta is undefined")
    beta = float(x_centered @ (y - y.mean())) / sxx
    alpha = float(y.mean() - beta * x.mean())
    residual = y - beta * x
    fitted_resid = y - (alpha + beta * x)
    ss_tot = float(((y - y.mean()) ** 2).sum())
    r_squared = 1.0 - float((fitted_resid**2).sum()) / ss_tot if ss_tot > 1e-18 else 0.0
    return BenchmarkStats(
        beta=beta,
        alpha_daily=alpha,
        alpha_annual=alpha * periods_per_year,
        r_squared=r_squared,
        residual_returns=tuple(float(r) for r in residual),
    )
