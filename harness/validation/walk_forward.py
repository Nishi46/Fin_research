"""Walk-forward stage.

Slices the base backtest's daily returns by the purged walk-forward test windows and requires
(a) the pooled out-of-sample net Sharpe to exceed `wf_min_oos_sharpe` and (b) at least
`wf_min_positive_fold_fraction` of the folds to have a positive net return.

Strategies are fixed-parameter functions, so nothing is fitted per fold. This stage therefore
measures consistency across time, NOT out-of-sample generalisation. Selection bias across
candidates is the trial-adjusted stage's job.
"""

import numpy as np

from contracts import Stage
from harness.api import BacktestResult, StageResult
from harness.metrics.core import sharpe
from harness.thresholds import DEFAULT_THRESHOLDS, Thresholds
from harness.validation.splitter import PurgedWalkForwardSplitter


def walk_forward_stage(
    result: BacktestResult, thresholds: Thresholds = DEFAULT_THRESHOLDS
) -> StageResult:
    def fail(reason: str, metrics: dict[str, float] | None = None) -> StageResult:
        return StageResult(
            stage=Stage.WALK_FORWARD, passed=False, reason=reason, metrics=metrics or {}
        )

    index = {ts: i for i, ts in enumerate(result.timestamps)}
    try:
        splits = list(
            PurgedWalkForwardSplitter().splits(
                result.timestamps,
                n_splits=thresholds.wf_n_splits,
                purge=0,
                embargo=thresholds.embargo_min_bars,
            )
        )
    except ValueError as err:
        return fail(f"cannot split the sample: {err}")

    returns = np.asarray(result.daily_returns, dtype=np.float64)
    folds = [returns[index[s.test_start] : index[s.test_end] + 1] for s in splits]
    fold_returns = [float(np.prod(1.0 + f) - 1.0) for f in folds]
    pooled = np.concatenate(folds)
    positive = sum(1 for r in fold_returns if r > 0.0) / len(folds)
    oos_sharpe = sharpe(pooled.tolist())
    metrics = {
        "n_folds": float(len(folds)),
        "oos_sharpe": oos_sharpe,
        "positive_fold_fraction": positive,
        "n_oos_obs": float(len(pooled)),
    }
    if oos_sharpe <= thresholds.wf_min_oos_sharpe:
        return fail(
            f"pooled out-of-sample Sharpe {oos_sharpe:.2f} is not above "
            f"{thresholds.wf_min_oos_sharpe:.2f}",
            metrics,
        )
    if positive < thresholds.wf_min_positive_fold_fraction:
        return fail(
            f"only {positive:.0%} of folds have a positive return; at least "
            f"{thresholds.wf_min_positive_fold_fraction:.0%} required",
            metrics,
        )
    return StageResult(
        stage=Stage.WALK_FORWARD,
        passed=True,
        reason=f"OOS Sharpe {oos_sharpe:.2f}, {positive:.0%} of folds positive",
        metrics=metrics,
    )
