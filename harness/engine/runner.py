"""Runs one backtest and packages it as a `BacktestResult` for the validation stages.

Capital is a required input, never a default. `cost_multiplier` above 1 is the cost-sensitivity
run mode (2.0 doubles every cost). The seed and code version are recorded so a result can be
reproduced; the harness itself draws no random numbers, so the seed is for strategies and
stages that do.
"""

import os
import subprocess
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

from contracts import CapitalConfig, CostModel, DataProvider, Strategy
from harness.api import BacktestResult, Executor, Sizer
from harness.costs.model import RealCostModel, ScaledCostModel
from harness.engine.backtest import BacktestConfig, Backtester, SimulatedExecutor
from harness.engine.sizing import DefaultSizer

CODE_VERSION_ENV = "LAB_CODE_VERSION"
GIT_TIMEOUT_SECONDS = 10
UNKNOWN_VERSION = "unknown"


def detect_code_version() -> str:
    """Git commit of this checkout, with `-dirty` if there are uncommitted changes.

    `LAB_CODE_VERSION` overrides it (for runners without git). Falls back to `unknown`, which
    marks a result as not reproducible by code version.
    """
    override = os.environ.get(CODE_VERSION_ENV)
    if override:
        return override
    cwd = Path(__file__).resolve().parent
    try:
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=cwd,
            capture_output=True,
            text=True,
            check=True,
            timeout=GIT_TIMEOUT_SECONDS,
        ).stdout.strip()
        status = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=cwd,
            capture_output=True,
            text=True,
            check=True,
            timeout=GIT_TIMEOUT_SECONDS,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return UNKNOWN_VERSION
    if not head:
        return UNKNOWN_VERSION
    return f"{head}-dirty" if status else head


def run_backtest(
    *,
    provider: DataProvider,
    strategy: Strategy,
    symbols: Sequence[str],
    start: datetime,
    end: datetime,
    capital: CapitalConfig,
    cost_model: CostModel | None = None,
    sizer: Sizer | None = None,
    executor: Executor | None = None,
    config: BacktestConfig | None = None,
    cost_multiplier: float = 1.0,
    seed: int = 0,
    code_version: str | None = None,
) -> BacktestResult:
    base_costs = cost_model if cost_model is not None else RealCostModel()
    costs = ScaledCostModel(base_costs, cost_multiplier)  # validates multiplier >= 1
    snapshot = provider.snapshot()
    output = Backtester(
        provider=provider,
        strategy=strategy,
        capital=capital,
        cost_model=costs,
        sizer=sizer if sizer is not None else DefaultSizer(),
        executor=executor if executor is not None else SimulatedExecutor(),
        config=config,
    ).run(symbols, start, end)
    return BacktestResult(
        capital=capital,
        snapshot_id=snapshot.id,
        cost_multiplier=cost_multiplier,
        seed=seed,
        code_version=code_version if code_version is not None else detect_code_version(),
        survivorship_biased=snapshot.survivorship_biased,
        timestamps=output.timestamps,
        daily_returns=output.daily_returns,
        trades=tuple(
            trade.model_copy(update={"crossed_event": flagged})
            for trade, flagged in zip(output.trades, output.earnings_hold_flags, strict=True)
        ),
    )
