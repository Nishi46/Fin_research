# Phase 1 Task List: Harness

For owner review at the Phase 0 gate. Phase 1 builds the grader: the code that decides whether a strategy is any good. It is the most rigorous phase, and its output is locked read-only to every agent once you have reviewed it.

## Before any teammate writes code

1. Activate the Phase 0 lock (see `docs/PROTECTIONS.md`) and run the manual verification.
2. Confirm how Agent Teams is enabled and that teammates run the project hooks. If they do not, switch to worktrees plus prompt-level path rules and tell the owner.
3. Lead freezes the dependency list for the phase and adds it (see "Dependencies").

## Teammates

All four build against `contracts/` and pass `tests/contract_suite/`. None may edit `contracts/`, `pyproject.toml`, `.github/`, `.claude/`, or another teammate's directory.

### data-engineer: `harness/data/`

Build the free-data layer.
- A `DataProvider` for Alpaca free daily bars (adjusted), with a local Parquet cache and incremental fetch that respects rate limits.
- Cross-check providers (Stooq, yfinance) used only for reconciliation, never as the sole source.
- Reconciliation: compare adjusted closes across sources; fail a snapshot when they disagree beyond a threshold. Split and dividend sanity checks; gap and stale-bar detection.
- Index membership reconstructed from free change-history lists, used for the universe only.
- Content-hash snapshot IDs; every snapshot flagged `survivorship_biased=True`.
- Volume handling: use consolidated volume where available or a conservative scaling, since IEX-only volume understates liquidity.
- Report which market regimes the available history actually covers.

Acceptance: passes `test_data_contract.py`; a 2-for-1 split does not appear as a 50% drop; two sources disagreeing fails the snapshot; no network in tests (recorded fixtures).

### validation-engineer: `harness/validation/` and `harness/registry/`

Build the overfitting defences.
- Purged walk-forward splitter with embargo, implementing `Splitter`. No random k-fold anywhere.
- Deflated Sharpe Ratio using `Registry.count()`, and probability of backtest overfitting (CSCV).
- Sample-size and regime floors; cost-doubling and parameter-stability checks as callable stages.
- Hidden final holdout: lives outside the repo, returns only pass/fail or coarse scores to the proposer.
- Lookahead check: truncate data at time t and require signals at t to equal those from the full dataset (the baseline strategy already has this test as a template).
- SQLite `Registry`, `RunLog` and `CommandInbox` implementations on `contracts/sql/registry_sqlite.sql`, passing the contract suite. Postgres implementation after a live Postgres test is available.

Acceptance: passes `test_registry_contract.py` and `test_runtime_contract.py`; DSR of the best of many random strategies is not significant; a leaky strategy fails the lookahead check.

### execution-modeler: `harness/costs/`, `harness/engine/`, `harness/metrics/`

Build the honest simulator.
- Event-driven backtester that is the same code path the live engine will use.
- Real `CostModel`: spread, commission, slippage and size-dependent impact; capital is an explicit input; liquidity cap as a share of average daily dollar volume.
- Stops fill at the next available price after a gap, not at the stop price.
- Metrics: Profit Factor, Sharpe, Calmar, max drawdown, returns beyond SPY exposure.

Acceptance: passes `test_cost_contract.py`; the same strategy at several capital levels gives different position counts and costs with identical signals; doubling costs is a supported run mode.

### adversary: `tests/adversarial/`

The breaker. Writes strategies and tests designed to defeat the harness, and may not weaken them to make the harness pass.
- A leaky strategy (peeks at the future).
- A pure-noise strategy selected as the best of N trials.
- A cost-blind edge that disappears when costs double.
- A winner that made all its money in one regime.
- A single-parameter-peak overfit.
- Attempts to bypass `DataView`.

Acceptance: every one of these is rejected by the harness pipeline at the expected stage, and the rejections are recorded in the registry.

## Dependencies

Libraries are added by the lead in a separate reviewed change. Expected: pandas, numpy, scipy, pyarrow, duckdb, alpaca-py, exchange_calendars, quantstats or empyrical. Anything else is requested by the teammate with a reason. Verify licences before adding any statistics library for DSR or PBO, or implement them directly from the published papers.

## Phase 1 gate (owner)

- Adversary suite green and `make check` green.
- Owner reviews the harness diff.
- Then add `harness/` to `.claude/protected_paths.txt`. From this point no agent writes to it.
