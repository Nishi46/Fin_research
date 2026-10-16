# Spec: Phase 1 Harness

**Created**: 2026-10-15
**Status**: draft
**Author**: Nishi Kantamneni
**Epic**: none (build step 1 in `docs/project.md` §11; follows `phase-0-foundations`)

## Problem

There is no trustworthy grader yet. Phase 0 froze the contracts and fakes, but nothing real can tell a noise strategy from a good one. Until the harness exists, any backtest number is untrustworthy: it can hide lookahead, selection bias, omitted costs or a single lucky regime. Every later piece (risk layer, forward tests, proposer) depends on this being right.

## Goal

A real, immutable harness in `harness/` that, at the owner's Phase 1 gate:
- implements every frozen contract (`DataProvider`, `CostModel`, `Splitter`, `Registry`, `RunLog`, `CommandInbox`) and passes `tests/contract_suite/` with real implementations added;
- runs the fixed validation pipeline (lookahead, walk_forward, trial_adjusted, cost_sensitivity, stability, floors) end to end and logs every candidate to the registry;
- rejects each adversarial case at exactly its expected stage (table below), with the rejection recorded in the registry;
- uses the same event-driven code path the live engine will use;
- has all stage thresholds in one frozen config approved by the owner, so no teammate can tune the grader to make a case pass.

Then `harness/` is added to `.claude/protected_paths.txt` and no agent writes to it again.

## User Stories

- As the **owner**, I want to review the harness diff once against fixed contracts and a fixed adversary suite, so that I can trust "nothing passed" and "this passed" without re-auditing each strategy.
- As the **future proposer agent**, I want a read-only harness that returns a stage and a coarse result, so that I can iterate on strategies without being able to change or probe the grader.
- As a **Phase 1 teammate** (data-engineer, validation-engineer, execution-modeler, adversary), I want an owned directory and fixed contracts, so that I can build in parallel without collisions.
- As the **engine**, I want backtest and live to share one code path, so that there is no separate "live port" to drift.

## Requirements

### Must have

Ownership follows `docs/PHASE1_TASKS.md`. No teammate edits `contracts/`, `pyproject.toml`, `.github/`, `.claude/`, or another teammate's directory.

**R1. Data layer** (`harness/data/`, data-engineer)
1. Alpaca free daily-bars `DataProvider` (adjusted) with local Parquet cache, incremental fetch, rate-limit respect.
2. Stooq and yfinance used only for reconciliation, never as the sole source.
3. Reconciliation: compare adjusted closes across sources and fail the snapshot beyond a threshold; split and dividend sanity checks; gap and stale-bar detection.
4. Index membership reconstructed from free change-history lists, used for the universe only.
5. Content-hash snapshot IDs; every snapshot has `survivorship_biased=True`.
6. Volume uses consolidated volume where available, else conservative scaling (IEX-only understates liquidity).
7. Reports which market regimes the available history covers.

**R2. Validation and registry** (`harness/validation/`, `harness/registry/`, validation-engineer)
1. Purged walk-forward splitter with embargo implementing `Splitter`. No random k-fold anywhere.
2. Deflated Sharpe Ratio using `Registry.count()`, and probability of backtest overfitting (CSCV).
3. Sample-size and regime floors, cost-doubling and parameter-stability checks, each a callable stage.
4. Lookahead check: truncate data at t and require signals at t to equal those from the full dataset.
5. Hidden final holdout stored outside the repo; the proposer-facing interface returns only pass/fail or coarse scores.
6. SQLite `Registry`, `RunLog` and `CommandInbox` on `contracts/sql/registry_sqlite.sql`. Postgres implementation after a live Postgres test is available.

**R3. Execution modeling** (`harness/costs/`, `harness/engine/`, `harness/metrics/`, execution-modeler)
1. Event-driven backtester; same code path the live engine will use.
2. Real `CostModel`: spread, commission, slippage and size-dependent impact; capital is an explicit input; liquidity cap as a share of average daily dollar volume.
3. Stops fill at the next available price after a gap, not at the stop price.
4. Metrics: Profit Factor, Sharpe, Calmar, max drawdown, returns beyond SPY exposure.
5. Cost-doubling supported as a run mode.

**R4. Adversary suite** (`tests/adversarial/`, adversary)

The adversary writes strategies and tests designed to defeat the harness and may not weaken them to make the harness pass. A case counts as passed only if it is rejected at exactly its expected stage. Rejected earlier is also a failure, because it would not prove the expected defence works. Each case runs to completion and its rejection (stage reached) is recorded in the registry.

| Case | Must be rejected at | Why |
|---|---|---|
| Leaky strategy (peeks at the future) | `lookahead` | Signals at t differ when data is truncated at t |
| Pure noise, best of N trials | `trial_adjusted` | DSR not significant given N from `Registry.count()` |
| Cost-blind edge | `cost_sensitivity` | Profit vanishes at 2x costs |
| Single-regime winner | `floors` | Regime floor fails |
| Single-parameter-peak overfit | `stability` | Neighboring parameter values collapse |
| `DataView` bypass attempts | no stage: raise `LookaheadError`, or the lookahead stage catches them | The guard is the defence, not the pipeline |

**R5. Frozen threshold config.** One config holds every stage threshold. Defaults below are starting points chosen from common practice, not validated values. They are frozen and owner-approved at the Phase 1 gate; no teammate may change them. Any change is a contract-style change through the lead and the owner.

| Stage | Parameter | Default |
|---|---|---|
| `walk_forward` | Embargo | Equals label horizon, at least 1 bar |
| `trial_adjusted` | DSR must exceed | 0.95 probability (one-sided 5%) |
| `trial_adjusted` | PBO (CSCV) must stay under | 0.5 |
| `cost_sensitivity` | Cost multiplier | 2x; Profit Factor still above 1 |
| `stability` | Neighbor check | Holds within ±20% of peak across neighboring parameter values |
| `floors` | Minimum trades | 100 |
| `floors` | Regimes | Profitable in at least 2 of the regimes the data covers. Regimes are derived from the benchmark in the pinned snapshot (R7), not listed in this config. |
| `floors` | `regime_min_drawdown` | 0.15: a selloff regime is a decline of at least 15% from a record close |
| `floors` | `benchmark_symbol` | `SPY` |

**R6. Dependencies** are added by the lead in a separate reviewed change (expected: pandas, numpy, scipy, pyarrow, duckdb, alpaca-py, exchange_calendars, quantstats or empyrical). Verify licences before adding any statistics library for DSR or PBO, or implement them directly from the published papers.

**R7. Regimes are derived, not listed** (amendment, owner decision 2026-10-16; replaces the hand-written regime table).
1. `derive_regimes(bars, min_drawdown)` in `harness/data/regimes.py` is a pure function of daily benchmark closes. It returns contiguous, non-overlapping half-open `Regime`s that cover the data from its first bar to one day after its last.
2. Rule on closes: track the running record close. When a decline of at least `min_drawdown` from a record close occurs before the next record close, a **selloff** regime runs from that record close to the lowest close before the next record close. The stretches between selloffs are **advance** regimes. Dips smaller than `min_drawdown` stay inside an advance.
3. A selloff not yet followed by a new record is **open**: it ends one day after the last bar and is provisional. Completed episodes never change when new data arrives; only the open tail can.
4. Names are generated from dates (for example `selloff_2020-02-19`, `advance_2020-03-23`; an open selloff gets an `_open` suffix), so they are deterministic.
5. The pipeline derives regimes from the benchmark bars of the snapshot being validated. The regimes are therefore a function of the snapshot id, which every trial records, so results stay reproducible. The floors stage takes the regimes as an argument. No benchmark bars in the provider means the floors stage FAILS closed.
6. Regression anchor: a series that passes through the published S&P 500 peak and trough closes of 2018, 2020, 2022 and 2025 must derive exactly the boundaries the owner confirmed: 2018-09-20, 2018-12-24, 2020-02-19, 2020-03-23, 2022-01-03, 2022-10-12, 2025-02-19, 2025-04-08.
7. Known limits: boundaries are hindsight and apply to attributing past trades, never to a strategy's decisions. SPY from the free IEX feed, adjusted for dividends, can place a peak or trough a day or so away from the index close.

### Nice to have
- Postgres `Registry` implementation validated against a live Postgres.
- A single `make verify-harness` target running the adversary suite and the contract suite together.

### Out of scope
- Risk layer, broker integration, Alpaca order or paper code, pre-registered forward tests (build steps 2 to 4).
- Proposer agent and website (steps 5 and 6).
- Promotion thresholds for real money (owner-set, later).
- Setting up Neon or the live-engine GitHub Actions workflows.
- Changing any frozen contract. If one proves wrong, it goes through the lead and the owner.

## Data Model

No new tables. Implement against the frozen DDL in `contracts/sql/registry_sqlite.sql` (`trials`, `runs`, `commands`, `command_status_history`): UUID ids, TEXT not VARCHAR, TIMESTAMPTZ in UTC, no foreign keys, append-only enforced by triggers. No soft delete by design. Postgres DDL (`registry_postgres.sql`) has only been pattern-checked; validating it on a live Postgres is a Phase 1 task. Parquet files hold cached bars (local); DuckDB reads them.

## API Changes

None. This is an internal library. No HTTP endpoints, no website changes.

## UI Changes

None.

## Edge Cases

1. **Split shown as crash:** a 2-for-1 split must not appear as a 50% drop after adjustment.
2. **Sources disagree:** Alpaca versus Stooq or yfinance adjusted closes differ beyond threshold, so the snapshot fails and is not used.
3. **Gaps and stale bars:** missing days, repeated closes, a holiday handled wrongly.
4. **Overnight gap through a stop:** the fill is at the next available price, not the stop price; holds across earnings are flagged or excluded.
5. **IEX-only volume:** liquidity caps based on understated volume must use consolidated volume or conservative scaling.
6. **Capital changes:** the same signals at different capital levels give different position counts and costs; a pass at one level says nothing about another.
7. **Empty or tiny sample:** too few trades or too short a window fails the floors stage rather than producing a Sharpe.
8. **Trial count drift:** DSR must use the registry count at evaluation time, including failed and rejected trials.
9. **Registry rewrite attempts:** `INSERT OR REPLACE`, upserts, UPDATE and DELETE are blocked and tested.
10. **Holdout leakage:** proposer-facing results never include detailed holdout metrics.
11. **Duplicate trial id:** raises `DuplicateTrialError`, nothing is overwritten.

## Testing Criteria

Tests first for every component (the harness is the trust anchor).

**Happy path**
- Real implementations are added to the `params` lists in `tests/contract_suite/conftest.py` and pass `test_data_contract.py`, `test_cost_contract.py`, `test_splitter_contract.py`, `test_registry_contract.py`, `test_runtime_contract.py`.
- A genuine toy edge passes the full pipeline in a synthetic world built to contain one.
- Backtests are reproducible: pinned snapshot, seeded randomness, recorded code version.

**Edge cases and adversarial**
- All R4 cases rejected at exactly their expected stage, each recorded in the registry. `make check` stays green.
- DSR of the best of many random strategies is not significant.
- Two disagreeing sources fail the snapshot. No network in tests (recorded fixtures).
- Same strategy at several capital levels: identical signals, different position counts and costs.
- Doubling costs is a supported run mode.
- Property tests (hypothesis) on the splitter: train always ends before test starts, purge and embargo are honored, no overlap.

## Dependencies

- Frozen `contracts/` package and `tests/contract_suite/` (Phase 0, merged).
- Phase 0 guards: import-linter boundaries (harness must not import strategies, proposer, broker or risk), path-protection hook, CI.
- Library list in R6, added by the lead before teammates start.
- Agent Teams enabled and teammates running the project hooks. If they do not, fall back to worktrees plus prompt-level path rules and tell the owner.
- Alpaca free-tier keys in environment variables for data fetch (paper endpoint default); never committed.

## Gate 1 Checklist

**Completeness**
- [x] Problem clearly stated
- [x] Goal specific and measurable
- [x] At least one user story
- [x] Requirements split into must-have, nice-to-have, out of scope
- [x] Out of scope section exists

**Data model**
- [x] No new tables; existing DDL has standard columns and UTC timestamps
- [x] Column types correct (TEXT, UUID, TIMESTAMPTZ)
- [x] Soft delete strategy defined (none; append-only by design)

**API design**
- [x] Not applicable (no endpoints); stated explicitly

**Quality**
- [x] Edge cases listed (11)
- [x] Testing criteria for happy path
- [x] Testing criteria for edge cases
- [x] Dependencies listed

**Open items for the owner (not blockers for Gate 1)**
- The R5 threshold defaults are unvalidated and need your approval at the Phase 1 gate. The minimum-trades and regime floors in particular depend on how short the usable history turns out to be (about 2016 onward).
