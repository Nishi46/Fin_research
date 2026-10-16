"""Performance metrics on daily returns and per-trade P&L.

Conventions that matter for honesty:
- Undefined ratios (no variance, no trades) report 0.0, never a flattering infinity.
- Profit Factor and Calmar return `inf` only when there is profit and truly no loss or no
  drawdown. Stages must treat `inf` with suspicion, since it usually means too few trades.
- Drawdown is measured from starting capital, so an immediate loss counts.
"""

import math
from collections.abc import Sequence

import numpy as np

TRADING_DAYS_PER_YEAR = 252


def _checked(returns: Sequence[float]) -> np.ndarray:
    arr = np.asarray(returns, dtype=float)
    if not np.all(np.isfinite(arr)):
        raise ValueError("returns must be finite")
    if np.any(arr < -1.0):
        raise ValueError("a return cannot be below -100%")
    return arr


def profit_factor(pnls: Sequence[float]) -> float:
    """Gross profit divided by gross loss over trade P&L (net of costs)."""
    arr = np.asarray(pnls, dtype=float)
    if not np.all(np.isfinite(arr)):
        raise ValueError("pnls must be finite")
    gross_profit = float(arr[arr > 0].sum())
    gross_loss = float(-arr[arr < 0].sum())
    if gross_loss == 0.0:
        return math.inf if gross_profit > 0.0 else 0.0
    return gross_profit / gross_loss


def sharpe(returns: Sequence[float], periods_per_year: int = TRADING_DAYS_PER_YEAR) -> float:
    """Annualized Sharpe of daily returns, risk-free rate taken as zero."""
    arr = _checked(returns)
    if arr.size < 2:
        return 0.0
    std = float(arr.std(ddof=1))
    # Constant series have zero variance up to float noise; that is "undefined", not "great".
    if std <= 1e-12 * max(1.0, abs(float(arr.mean()))):
        return 0.0
    return float(arr.mean()) / std * math.sqrt(periods_per_year)


def equity_curve(returns: Sequence[float]) -> list[float]:
    """Growth of one unit of capital after each return."""
    arr = _checked(returns)
    return [float(x) for x in np.cumprod(1.0 + arr)]


def max_drawdown(returns: Sequence[float]) -> float:
    """Largest peak-to-trough loss as a positive fraction in [0, 1]."""
    arr = _checked(returns)
    if arr.size == 0:
        return 0.0
    curve = np.concatenate(([1.0], np.cumprod(1.0 + arr)))
    peak = np.maximum.accumulate(curve)
    return float(np.max(1.0 - curve / peak))


def annualized_return(
    returns: Sequence[float], periods_per_year: int = TRADING_DAYS_PER_YEAR
) -> float:
    """Geometric (compound) annualized return."""
    arr = _checked(returns)
    if arr.size == 0:
        return 0.0
    final = float(np.prod(1.0 + arr))
    if final <= 0.0:
        return -1.0
    return float(final ** (periods_per_year / arr.size) - 1.0)


def calmar(returns: Sequence[float], periods_per_year: int = TRADING_DAYS_PER_YEAR) -> float:
    """Annualized return divided by max drawdown."""
    ann = annualized_return(returns, periods_per_year)
    drawdown = max_drawdown(returns)
    if drawdown == 0.0:
        return math.inf if ann > 0.0 else 0.0
    return ann / drawdown
