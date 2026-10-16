"""Floors stage: minimum sample size and regime spread.

A result that does not clear the floors is rejected before any performance number is looked at:
the metrics returned describe the sample (trade count, per-regime P&L), never a Sharpe ratio,
so a tiny sample cannot produce a number that gets quoted.

A regime counts as profitable when the net P&L (after costs) of trades that exit inside it is
positive. Trades that exit outside every regime are ignored, which is why the regimes must
cover the data window. The regimes are an argument: the pipeline derives them from the
benchmark bars of the snapshot being validated.
"""

from collections.abc import Sequence

from contracts import Stage
from harness.api import BacktestResult, Regime, StageResult
from harness.thresholds import DEFAULT_THRESHOLDS, Thresholds


def floors_stage(
    result: BacktestResult,
    regimes: Sequence[Regime],
    thresholds: Thresholds = DEFAULT_THRESHOLDS,
) -> StageResult:
    n_trades = len(result.trades)
    outside = sum(1 for t in result.trades if not any(r.contains(t.exit_ts) for r in regimes))
    # Flagged, not enforced: shows when the regimes no longer cover the trades.
    metrics: dict[str, float] = {
        "n_trades": float(n_trades),
        "trades_outside_regimes": float(outside),
        "n_regimes": float(len(regimes)),
    }

    if n_trades < thresholds.min_trades:
        return StageResult(
            stage=Stage.FLOORS,
            passed=False,
            reason=f"{n_trades} trades is below the minimum of {thresholds.min_trades}",
            metrics=metrics,
        )

    profitable = 0
    for regime in regimes:
        pnl = sum(t.pnl for t in result.trades if regime.contains(t.exit_ts))
        metrics[f"regime_pnl.{regime.name}"] = pnl
        if pnl > 0:
            profitable += 1
    metrics["profitable_regimes"] = float(profitable)

    if profitable < thresholds.min_profitable_regimes:
        return StageResult(
            stage=Stage.FLOORS,
            passed=False,
            reason=(
                f"profitable in {profitable} regimes; at least "
                f"{thresholds.min_profitable_regimes} required"
            ),
            metrics=metrics,
        )
    return StageResult(
        stage=Stage.FLOORS,
        passed=True,
        reason=f"{n_trades} trades, profitable in {profitable} regimes",
        metrics=metrics,
    )
