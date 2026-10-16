"""Probability of Backtest Overfitting by Combinatorially Symmetric Cross-Validation.

From Bailey, Borwein, Lopez de Prado and Zhu, "The Probability of Backtest Overfitting" (2015):

1. Split the T x N matrix of strategy returns into S equal row blocks.
2. For each of the C(S, S/2) ways to choose S/2 blocks as in-sample (IS), the rest are
   out-of-sample (OOS).
3. Find the strategy n* with the best IS Sharpe ratio, and its relative OOS rank
   omega = rank / (N + 1), rank 1 being the worst of the N.
4. logit = ln(omega / (1 - omega)). PBO is the share of combinations with logit <= 0, i.e. the
   IS winner finished at or below the OOS median.

Judgement calls: the performance measure is the per-period Sharpe ratio; ties in OOS rank use
the average rank; rows that do not fill a block (T mod S) are dropped from the end; a column
with zero variance has Sharpe 0.
"""

from itertools import combinations

import numpy as np
from numpy.typing import NDArray
from scipy.stats import rankdata  # type: ignore[import-untyped]

_MIN_ROWS_PER_BLOCK = 2


def _sharpe(
    total: NDArray[np.float64], total_sq: NDArray[np.float64], n: int
) -> NDArray[np.float64]:
    mean = total / n
    var = (total_sq - n * mean**2) / (n - 1)
    std = np.sqrt(np.maximum(var, 0.0))
    safe = np.where(std > 0.0, std, 1.0)
    return np.where(std > 0.0, mean / safe, 0.0)


def cscv_logits(returns: NDArray[np.float64], n_blocks: int = 16) -> NDArray[np.float64]:
    """Logit of the IS-winner's relative OOS rank, one per CSCV combination."""
    if n_blocks < 2 or n_blocks % 2 != 0:
        raise ValueError("n_blocks must be a positive even number")
    if returns.ndim != 2 or returns.shape[1] < 2:
        raise ValueError("need a T x N matrix with at least two strategies")
    t_rows, n_strategies = returns.shape
    rows_per_block = t_rows // n_blocks
    if rows_per_block < _MIN_ROWS_PER_BLOCK:
        raise ValueError("too few rows for the requested number of blocks")

    data = returns[: rows_per_block * n_blocks].reshape(n_blocks, rows_per_block, n_strategies)
    block_sum = data.sum(axis=1)
    block_sq = (data**2).sum(axis=1)
    half = n_blocks // 2
    n_obs = half * rows_per_block

    in_sample = np.array(list(combinations(range(n_blocks), half)))
    mask = np.zeros((len(in_sample), n_blocks), dtype=bool)
    np.put_along_axis(mask, in_sample, True, axis=1)
    out_sample = np.array([np.flatnonzero(~row) for row in mask])

    is_sr = _sharpe(block_sum[in_sample].sum(axis=1), block_sq[in_sample].sum(axis=1), n_obs)
    oos_sr = _sharpe(block_sum[out_sample].sum(axis=1), block_sq[out_sample].sum(axis=1), n_obs)

    winners = is_sr.argmax(axis=1)
    ranks = rankdata(oos_sr, axis=1)
    omega = ranks[np.arange(len(winners)), winners] / (n_strategies + 1)
    return np.asarray(np.log(omega / (1.0 - omega)), dtype=np.float64)


def probability_of_backtest_overfitting(returns: NDArray[np.float64], n_blocks: int = 16) -> float:
    return float(np.mean(cscv_logits(returns, n_blocks) <= 0.0))
