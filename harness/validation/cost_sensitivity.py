"""Cost-sensitivity stage: the edge must survive a multiple of trading costs.

The caller injects `run`, a closure that re-runs the same backtest at a given cost multiplier
(in production a wrapper over `harness.engine.runner.run_backtest`). The multiplier and the
profit-factor floor come from the thresholds, never from here.
"""

import math
from collections.abc import Callable

from contracts import Stage
from harness.api import BacktestResult, StageResult
from harness.metrics.core import profit_factor
from harness.thresholds import DEFAULT_THRESHOLDS, Thresholds

CostRunner = Callable[[float], BacktestResult]


def _pf(result: BacktestResult) -> float:
    return profit_factor([t.pnl for t in result.trades])


def cost_sensitivity_stage(
    base: BacktestResult,
    run: CostRunner,
    thresholds: Thresholds = DEFAULT_THRESHOLDS,
) -> StageResult:
    multiplier = thresholds.cost_multiplier
    scaled = run(multiplier)
    metrics = {
        "cost_multiplier": multiplier,
        "profit_factor_base": _pf(base),
        "profit_factor_scaled": _pf(scaled),
        "n_trades_scaled": float(len(scaled.trades)),
    }

    def result(passed: bool, reason: str) -> StageResult:
        return StageResult(
            stage=Stage.COST_SENSITIVITY, passed=passed, reason=reason, metrics=metrics
        )

    if not math.isclose(scaled.cost_multiplier, multiplier):
        return result(
            False,
            f"runner returned cost multiplier {scaled.cost_multiplier}, expected {multiplier}",
        )
    if not scaled.trades:
        return result(False, f"no trades at {multiplier}x costs")
    floor = thresholds.cost_min_profit_factor
    if metrics["profit_factor_scaled"] <= floor:
        return result(
            False,
            f"profit factor {metrics['profit_factor_scaled']:.2f} at {multiplier}x costs "
            f"is not above {floor:.2f}",
        )
    return result(
        True,
        f"profit factor {metrics['profit_factor_scaled']:.2f} at {multiplier}x costs "
        f"(base {metrics['profit_factor_base']:.2f})",
    )
