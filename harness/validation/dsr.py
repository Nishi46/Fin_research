"""Deflated Sharpe Ratio (Bailey and Lopez de Prado, 2014) and the trial-adjusted stage.

All Sharpe ratios here are per-period (not annualized), as the formulas require.

    SR0 = sqrt(V) * ((1 - g) * z(1 - 1/N) + g * z(1 - 1/(N * e)))      g = Euler-Mascheroni
    DSR = Phi((SR - SR0) * sqrt(T - 1) / sqrt(1 - skew * SR + (kurt - 1) / 4 * SR^2))

`V` is the variance of the Sharpe ratios across the N trials, `kurt` is raw kurtosis (3 for a
normal), and N is `Registry.count()` at evaluation time, so every logged trial, failures
included, deflates the next one.

Judgement calls the owner should check:
* N = 1 gives SR0 = 0 (no selection), i.e. DSR reduces to the Probabilistic Sharpe Ratio.
* V comes from the `sharpe_per_period` metric of logged trials when at least two have it.
  Otherwise it falls back to the sampling variance of the candidate's own Sharpe estimate
  (the null-hypothesis spread of one estimate). Trials of different strategies usually vary
  more than that, so the fallback is the lenient choice; the pipeline should record
  `sharpe_per_period` on every trial so the data-driven estimate is used.
* If the candidate itself is already in the registry when this runs, it is part of N.
"""

import math
from collections.abc import Mapping

import numpy as np
from numpy.typing import NDArray
from scipy.stats import kurtosis as _kurtosis  # type: ignore[import-untyped]
from scipy.stats import norm
from scipy.stats import skew as _skew

from contracts import Registry, Stage
from harness.api import BacktestResult, StageResult
from harness.thresholds import DEFAULT_THRESHOLDS, Thresholds
from harness.validation.pbo import probability_of_backtest_overfitting

EULER_MASCHERONI = 0.5772156649015329
SHARPE_METRIC = "sharpe_per_period"
# Set to 1.0 by the pipeline when a trial died before a return series existed (value is a 0.0
# placeholder that must not enter the cross-trial variance).
SHARPE_MISSING_FLAG = "sharpe_per_period_missing"
_MIN_OBS = 2


def expected_max_sharpe(n_trials: int, sr_variance: float) -> float:
    """SR0: the Sharpe ratio the best of `n_trials` unskilled trials is expected to reach."""
    if n_trials < 1:
        raise ValueError("n_trials must be at least 1")
    if sr_variance < 0:
        raise ValueError("sr_variance must be non-negative")
    if n_trials == 1:
        return 0.0
    g = EULER_MASCHERONI
    bracket = (1.0 - g) * norm.ppf(1.0 - 1.0 / n_trials) + g * norm.ppf(
        1.0 - 1.0 / (n_trials * math.e)
    )
    return math.sqrt(sr_variance) * float(bracket)


def deflated_sharpe_ratio(
    sharpe: float,
    *,
    n_obs: int,
    n_trials: int,
    sr_variance: float,
    skew: float = 0.0,
    kurtosis: float = 3.0,
) -> float:
    """Probability that the true Sharpe ratio exceeds the selection-bias benchmark SR0."""
    if n_obs < _MIN_OBS:
        raise ValueError("n_obs must be at least 2")
    sr0 = expected_max_sharpe(n_trials, sr_variance)
    denominator = 1.0 - skew * sharpe + (kurtosis - 1.0) / 4.0 * sharpe**2
    if denominator <= 0.0:
        raise ValueError("moments are inconsistent with this Sharpe ratio (variance <= 0)")
    z = (sharpe - sr0) * math.sqrt(n_obs - 1) / math.sqrt(denominator)
    return float(norm.cdf(z))


def sharpe_moments(returns: NDArray[np.float64]) -> tuple[float, float, float]:
    """Per-period Sharpe (sample std), skewness and raw kurtosis of a return series."""
    std = float(np.std(returns, ddof=1))
    sharpe = float(np.mean(returns)) / std if std > 0.0 else 0.0
    return sharpe, float(_skew(returns)), float(_kurtosis(returns, fisher=False))


def trial_sharpe_variance(registry: Registry) -> float | None:
    """Variance of logged per-period Sharpe ratios, or None if fewer than two were recorded."""
    values = [t.metrics[SHARPE_METRIC] for t in registry.list() if SHARPE_METRIC in t.metrics]
    if len(values) < _MIN_OBS:
        return None
    return float(np.var(values, ddof=1))


def _fail(reason: str, metrics: Mapping[str, float] | None = None) -> StageResult:
    return StageResult(
        stage=Stage.TRIAL_ADJUSTED, passed=False, reason=reason, metrics=dict(metrics or {})
    )


def trial_adjusted_stage(
    result: BacktestResult,
    registry: Registry,
    *,
    trial_returns: NDArray[np.float64] | None = None,
    sr_variance: float | None = None,
    n_trials: int | None = None,
    pbo_gating: bool = False,
    thresholds: Thresholds = DEFAULT_THRESHOLDS,
) -> StageResult:
    """Fail unless DSR clears `dsr_min_probability`.

    With `pbo_gating` it also fails when PBO is strictly above `pbo_max`.

    `n_trials` overrides `Registry.count()`; the pipeline passes count + 1 so the candidate (not
    yet appended) counts exactly once. `trial_returns` is an optional T x N matrix of the return
    series of every configuration tried for this candidate; when given, PBO is computed and
    recorded. It fails the stage only if the caller opts in with `pbo_gating` (the pipeline never
    does: over near-identical parameter neighbours PBO measures which neighbour wins, not
    overfitting), and then only when strictly above `pbo_max`.
    """
    returns = np.asarray(result.daily_returns, dtype=np.float64)
    if len(returns) < _MIN_OBS:
        return _fail(f"too few observations ({len(returns)}) to estimate a Sharpe ratio")
    sharpe, skew, kurt = sharpe_moments(returns)
    if float(np.std(returns, ddof=1)) == 0.0:
        return _fail("returns have zero variance; Sharpe ratio is undefined")

    n_trials = n_trials if n_trials is not None else max(registry.count(), 1)
    variance = sr_variance if sr_variance is not None else trial_sharpe_variance(registry)
    if variance is None:
        denom = 1.0 - skew * sharpe + (kurt - 1.0) / 4.0 * sharpe**2
        variance = max(denom, 0.0) / (len(returns) - 1)

    metrics = {
        "sharpe_per_period": sharpe,
        "n_trials": float(n_trials),
        "n_obs": float(len(returns)),
        "sr_variance": variance,
        "sr0": expected_max_sharpe(n_trials, variance),
        "pbo_computed": 0.0,
        "pbo_gating": 1.0 if pbo_gating else 0.0,
    }
    try:
        dsr = deflated_sharpe_ratio(
            sharpe,
            n_obs=len(returns),
            n_trials=n_trials,
            sr_variance=variance,
            skew=skew,
            kurtosis=kurt,
        )
    except ValueError as err:
        return _fail(f"DSR undefined: {err}", metrics)
    metrics["dsr"] = dsr

    if trial_returns is not None:
        try:
            pbo = probability_of_backtest_overfitting(trial_returns)
        except ValueError as err:
            if pbo_gating:
                return _fail(f"PBO cannot be computed: {err}", metrics)
        else:
            metrics["pbo"] = pbo
            metrics["pbo_computed"] = 1.0
            if pbo_gating and pbo > thresholds.pbo_max:
                return _fail(
                    f"PBO {pbo:.3f} is above the maximum {thresholds.pbo_max:.3f}", metrics
                )

    if dsr < thresholds.dsr_min_probability:
        return _fail(
            f"DSR {dsr:.3f} is below {thresholds.dsr_min_probability:.3f} after {n_trials} trials",
            metrics,
        )
    return StageResult(
        stage=Stage.TRIAL_ADJUSTED,
        passed=True,
        reason=f"DSR {dsr:.3f} after {n_trials} trials",
        metrics=metrics,
    )
