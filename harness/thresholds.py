"""Every validation rule, in one frozen place.

Owner-approved at the Phase 1 gate. No teammate may change a value to make a case pass: a
change is a contract-style change that goes through the lead to the owner.

The values are starting points chosen from common practice, not validated numbers. The owner
approved the walk-forward, neighbour and lookahead values on 2026-10-16, asked for the cost
defaults to live here, chose derived regimes over a hand-written table, required a minimum
t-statistic of 2 for the holdout, and moved the data reconciliation tolerances here.

This object holds rules, not data. Regimes are derived at validation time from the benchmark in
the pinned data snapshot (`harness.data.regimes.derive_regimes`) using `regime_min_drawdown`, so
they are a function of the snapshot id that every trial records.
"""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class Thresholds(BaseModel):
    # Unknown fields fail loudly: a typo or a stale field must never be silently ignored.
    model_config = ConfigDict(frozen=True, extra="forbid")

    # walk_forward: embargo is the label horizon, but never below this many bars.
    embargo_min_bars: int = Field(default=1, ge=1)
    # walk_forward: strategies are fixed-parameter functions with nothing fitted per fold, so this
    # stage measures consistency across time, not out-of-sample generalisation. Selection bias is
    # the trial_adjusted stage's job. Pass: pooled out-of-sample net Sharpe above the floor AND
    # enough folds with positive net return.
    wf_n_splits: int = Field(default=5, ge=2)
    wf_min_oos_sharpe: float = Field(default=0.0)
    wf_min_positive_fold_fraction: float = Field(default=0.6, gt=0.0, le=1.0)
    # lookahead: how many evenly spaced check times the pipeline truncates at.
    lookahead_check_count: int = Field(default=20, ge=1)
    # trial_adjusted
    dsr_min_probability: float = Field(default=0.95, gt=0.0, lt=1.0)
    pbo_max: float = Field(default=0.5, gt=0.0, le=1.0)
    # Cross-trial PBO (owner decision 2026-10-20): CSCV over the candidate plus up to pbo_max_peers
    # earlier trials of the same group. It gates only once the group has pbo_min_trials trials,
    # because below that the estimate is too erratic (pure noise gave 0.99, 0.73 and 0.51 at 21,
    # 101 and 201 columns). 16 blocks at 201 columns takes about 0.23s.
    pbo_min_trials: int = Field(default=30, ge=2)
    pbo_max_peers: int = Field(default=200, ge=1)
    pbo_n_blocks: int = Field(default=16, ge=2)
    # cost_sensitivity: re-run at this multiple of costs; profit factor must stay above the floor.
    cost_multiplier: float = Field(default=2.0, gt=1.0)
    cost_min_profit_factor: float = Field(default=1.0, gt=0.0)
    # stability: performance must hold within this fraction of the peak across neighbors.
    stability_tolerance: float = Field(default=0.20, gt=0.0, lt=1.0)
    # Neighbours of a numeric parameter p are p*(1 +/- f) for each f here (integers rounded,
    # at least 1 step away, duplicates dropped).
    stability_neighbor_fractions: tuple[Annotated[float, Field(gt=0.0, lt=1.0)], ...] = Field(
        default=(0.10, 0.20), min_length=1
    )
    # holdout: minimum t-statistic of the holdout's net daily returns (mean / standard error). It
    # scales with the holdout's length, where a fixed Sharpe is a weaker bar on a short window.
    holdout_min_t_stat: float = Field(default=2.0, gt=0.0)
    # Owner decision 2026-10-18: the holdout is the most recent 2 years of data, and the gate
    # allows 20 queries in total (each new code version spends one) before it refuses.
    holdout_years: int = Field(default=2, ge=1)
    holdout_max_queries: int = Field(default=20, ge=1)
    # Holdout carve-out (owner approved 2026-10-18). Embargo = 2x the longest 5-day hold, so no
    # research label or position can span the boundary. The holdout must contain at least this
    # fraction of its expected sessions, and research needs a minimum history to be worth grading.
    holdout_embargo_sessions: int = Field(default=10, ge=1)
    holdout_min_session_fraction: float = Field(default=0.9, gt=0.0, le=1.0)
    holdout_min_research_years: int = Field(default=3, ge=1)
    # Data reconciliation between sources. Calibrated 2026-10-18 (owner approved) on Alpaca IEX vs
    # yfinance, 10 large caps, 3 years: p99.9 of per-bar differences 0.88%, 4 of 7500 bars over 1%.
    # Re-checked the same day on the SIP feed (now the default), 45 symbols, 2016-2026: median
    # difference 0.00%, p99 0.05%, about 0.02% of bars over 1%. The price tolerance stays far
    # below real errors (splits, bad ticks); the disagree fraction is 10x or more above observed.
    # Recalibrate when the universe widens.
    reconcile_price_tolerance: float = Field(default=0.01, gt=0.0, lt=1.0)
    reconcile_max_disagree_fraction: float = Field(default=0.005, ge=0.0, lt=1.0)
    reconcile_min_overlap: float = Field(default=0.9, gt=0.0, le=1.0)
    # Cost model defaults. They decide what survives cost doubling, so they are frozen here and
    # the cost model, sizer and backtester read them from this object.
    cost_default_spread_bps: float = Field(default=2.0, ge=0.0)
    cost_slippage_bps: float = Field(default=1.0, ge=0.0)
    cost_commission_per_share: float = Field(default=0.0, ge=0.0)
    cost_impact_bps_at_full_adv: float = Field(default=150.0, ge=0.0)
    max_adv_participation: float = Field(default=0.05, gt=0.0, le=1.0)
    # floors: regimes are derived from the benchmark's closes. A selloff regime is a decline of at
    # least this fraction from a record close (the same rule that reproduces the 2018, 2020, 2022
    # and 2025 drawdowns), and the strategy must be profitable in this many regimes.
    benchmark_symbol: str = Field(default="SPY", min_length=1, pattern=r"^\S+$")
    regime_min_drawdown: float = Field(default=0.15, gt=0.0, lt=1.0)
    min_trades: int = Field(default=100, ge=1)
    min_profitable_regimes: int = Field(default=2, ge=1)

    @field_validator("pbo_n_blocks")
    @classmethod
    def _blocks_even(cls, value: int) -> int:
        if value % 2 != 0:
            raise ValueError("pbo_n_blocks must be even")
        return value

    @model_validator(mode="after")
    def _pbo_gate_reachable(self) -> "Thresholds":
        if self.pbo_max_peers + 1 < self.pbo_min_trials:
            raise ValueError("pbo gate not reachable: pbo_max_peers + 1 < pbo_min_trials")
        return self


DEFAULT_THRESHOLDS = Thresholds()
