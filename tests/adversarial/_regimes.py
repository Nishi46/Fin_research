"""Per-regime profit as the floors stage defines it: trades are attributed by exit date."""

from collections.abc import Sequence

from harness.api import BacktestResult, Regime


def pnl_by_regime(result: BacktestResult, regimes: Sequence[Regime]) -> dict[str, float]:
    return {r.name: sum(t.pnl for t in result.trades if r.contains(t.exit_ts)) for r in regimes}
