# Plan: Phase 1 Harness

**Spec**: .planning/specs/phase-1-harness.md
**Epic**: none
**Created**: 2026-10-15
**Status**: draft

## Stack and starting point

Python only (no Kotlin or Next.js files), so the Kotlin/Micronaut and Next.js rules do not apply. The "pattern" is the layering in `CLAUDE.md` and the Phase 0 code that is now on `main`:

- `contracts/` (frozen, locked): `Bar`, `Snapshot`, `DataProvider`, `DataView`, `Order`, `MarketState`, `CostModel`, `Split`, `Splitter`, `Registry`/`Trial`, `RunLog`, `CommandInbox`, `Stage`.
- `fakes/` holds one in-memory implementation per Protocol, and `tests/contract_suite/` is the shared suite every real implementation must pass.
- `harness/` is an empty package, currently unlocked. Import-linter already says harness must not import `strategies`, `proposer`, `broker` or `risk`.
- Locked paths (agents cannot write): `contracts/`, `.github/`, `.claude/`, `pyproject.toml`, `tools/`, `engine/`, `tests/contract_suite/`, `tests/guards/`, `docs/PROTECTIONS.md`.

Teammates (from `docs/PHASE1_TASKS.md`): **data-engineer** (`harness/data/`), **validation-engineer** (`harness/validation/`, `harness/registry/`), **execution-modeler** (`harness/costs/`, `harness/engine/`, `harness/metrics/`), **adversary** (`tests/adversarial/`). The **lead** owns shared glue and everything in a locked path, via the owner.

**Git:** per the standing rule, no agent commits, pushes, merges or opens PRs. "One task = one reviewable change"; the owner commits.

## Problems this plan resolves (found while reading Phase 0)

These are conflicts between the spec and the locks. They need a decision before teammates start. Recommendations are marked.

1. **Teammates cannot register their implementations.** The spec says to add real implementations to the `params` lists in `tests/contract_suite/conftest.py`, but `tests/contract_suite/` is locked. *Recommended:* the owner makes one change to `conftest.py` so each fixture also collects factories from a harness-owned module (`harness/testing/factories.py`). After that, teammates register in their own directory and never touch the locked suite.
2. **Dependencies live in a locked file.** `pyproject.toml` and `uv.lock` are protected, and the spec says the lead adds libraries in a separate reviewed change. So the owner applies the frozen dependency list (Task 2).
3. **Nowhere to put harness-internal shared types.** Stages need a `BacktestResult`, a `StageResult`, a `Sizer` and a regime table that no frozen contract defines. Editing `contracts/` is the wrong move. *Recommended:* the lead writes `harness/api.py` once, before teammates start, and it is frozen like a contract for the phase. It is the only file all four teammates read.
4. **"Same code path as live" versus the locked top-level `engine/`.** The backtester lives in `harness/engine/`. It takes an injected `Executor` and a `Sizer` protocol so the live engine can later supply a broker-backed executor and the real risk-layer sizer. Wiring `engine/` to it is a Phase 3/4 task, not Phase 1. The harness ships a simple default sizer only for backtests; it must not be mistaken for the risk layer (build step 3).
5. **Pipeline orchestrator had no owner.** Assigned to the validation-engineer (`harness/validation/pipeline.py`); it consumes stage functions from the others through `harness/api.py`.
6. **Regime definitions are undecided.** The regime floor and the single-regime adversary case both need them. *Recommended:* a fixed, dated regime table in the thresholds config (for example the 2018 Q4 selloff, the 2020 crash and recovery, the 2022 bear market, and bull periods between), approved by the owner with the other thresholds. Alternative: derive regimes from a volatility/trend rule, which is more defensible but adds tuning freedom to the grader.

## Components

| Component | Type | Purpose |
|---|---|---|
| `harness/api.py` | shared types (lead) | `BacktestResult`, `TradeRecord`, `StageResult`, `Sizer` and `Executor` protocols, `Regime` |
| `harness/thresholds.py` | frozen config (lead) | Every stage threshold (spec R5) and the regime table; immutable; owner-approved |
| Data adjustments and quality | module | Split/dividend adjustment, sanity checks, gap and stale detection |
| Reconciler | module | Cross-source adjusted-close comparison; fails snapshots |
| Parquet cache + snapshot hasher | module | Local bar cache, content-hash snapshot IDs, `survivorship_biased=True` |
| Alpaca provider | `DataProvider` | Free daily adjusted bars, incremental fetch, rate limiting (HTTP injected, so no network in tests) |
| Stooq/yfinance cross-checkers | module | Reconciliation sources only |
| Membership reconstructor | module | Point-in-time index membership for the universe |
| Volume handler, regime reporter | module | Conservative volume scaling; which regimes the history covers |
| SQLite `Registry`, `RunLog`, `CommandInbox` | implementations | On `contracts/sql/registry_sqlite.sql` |
| Purged walk-forward splitter | `Splitter` | Purge and embargo; no k-fold |
| DSR, PBO (CSCV) | statistics | Trial-adjusted metrics, implemented from the papers |
| Lookahead check, floors, cost-sensitivity, stability | stages | Callable pipeline stages |
| Hidden holdout | module | Stored outside the repo; coarse results only |
| Pipeline orchestrator | orchestration | Fixed stage order, stops at first failure, writes every `Trial` (including failures) |
| Metrics, SPY-beta | module | Profit Factor, Sharpe, Calmar, max drawdown, returns beyond SPY |
| `CostModel` | implementation | Spread, commission, slippage, size-dependent impact, ADV liquidity cap |
| Backtester, stop/gap fills, runner | module | Event-driven loop; next-available-price stops; capital and cost-multiplier as inputs |
| Adversary strategies and worlds | tests | Six cases plus a positive control, seeded synthetic data |

## File Locations

| File | Location | Owner |
|---|---|---|
| `api.py`, `thresholds.py`, `testing/factories.py` | `harness/` | lead |
| `adjust.py`, `quality.py`, `reconcile.py`, `cache.py`, `snapshot.py`, `alpaca.py`, `ratelimit.py`, `crosscheck.py`, `membership.py`, `volume.py`, `regimes.py`, `provider.py` | `harness/data/` | data-engineer |
| `sqlite.py` (registry), `sqlite_runtime.py` (run log + inbox) | `harness/registry/` | validation-engineer |
| `splitter.py`, `dsr.py`, `pbo.py`, `lookahead.py`, `floors.py`, `cost_sensitivity.py`, `stability.py`, `holdout.py`, `pipeline.py` | `harness/validation/` | validation-engineer |
| `model.py` | `harness/costs/` | execution-modeler |
| `sizing.py`, `backtest.py`, `stops.py`, `runner.py` | `harness/engine/` | execution-modeler |
| `core.py`, `benchmark.py` | `harness/metrics/` | execution-modeler |
| `tests/harness/<area>/` | per area | same owner as the code |
| `worlds.py`, `strategies/*.py`, `test_*.py` | `tests/adversarial/` | adversary |
| `tests/data_fixtures/` (recorded bars) | `tests/` | data-engineer |

Adversary-written strategies live under `tests/adversarial/` and are never in the top-level `strategies/` sandbox.

## Files to Change

| File | What Changes | Why | Who |
|---|---|---|---|
| `pyproject.toml`, `uv.lock` | Add frozen dependency list (pandas, numpy, scipy, pyarrow, duckdb, alpaca-py, exchange_calendars, quantstats or empyrical) and an import-linter rule keeping `harness/` free of `engine/` and `broker/` | Spec R6 | **owner** (locked) |
| `tests/contract_suite/conftest.py` | Fixtures also collect factories from `harness/testing/factories.py` | Problem 1 | **owner** (locked) |
| `Makefile` | Add `verify-harness` (adversary + contract suite) | Nice to have | lead |
| `.claude/protected_paths.txt` | Add `harness/` after the Phase 1 gate | Spec goal | **owner** |
| `docs/CONTRACTS.md` | Short "Harness internals" pointer to `harness/api.py` | Teammate onboarding | lead |

## Tasks

Every logic task is **test first**: write the failing test, then the code. Each task is one reviewable change of at most 3 files, plus its tests.

### Phase 0: Pre-work (sequential, lead and owner; blocks all teammates)

| # | Task | Files | Spec |
|---|---|---|---|
| 1 | Verify Agent Teams runs the project hooks for teammates (probe: a teammate attempts a write to a locked path). If not, fall back to worktrees plus prompt-level path rules and tell the owner | note in `docs/PROTECTIONS.md` (owner) | Dependencies |
| 2 | **Owner:** apply the dependency freeze and the harness import-linter rule | `pyproject.toml`, `uv.lock` | R6 |
| 3 | **Owner:** make `conftest.py` collect factories from `harness/testing/factories.py` (empty list to start); lead supplies the module | `tests/contract_suite/conftest.py`, `harness/testing/factories.py` | Goal |
| 4 | Lead writes `harness/api.py` and `harness/thresholds.py`; tests assert thresholds are immutable and equal the spec R5 defaults | `harness/api.py`, `harness/thresholds.py`, `tests/harness/test_thresholds.py` | R5 |
| 5 | **Owner decision gate:** approve `api.py`, the regime table and the six items above. Then teammates start. | none | R5 |

### Phase 1: Execution modeling (execution-modeler; parallel with Phases 2 and 3)

| # | Task | Files | Spec |
|---|---|---|---|
| 6 | Metrics: Profit Factor, Sharpe, Calmar, max drawdown (known-value tests, flat and losing series) | `harness/metrics/core.py` + test | R3.4 |
| 7 | Returns beyond SPY exposure (beta regression; test a pure-beta series gives ~0 alpha) | `harness/metrics/benchmark.py` + test | R3.4 |
| 8 | Real `CostModel`: spread, commission, slippage, impact; larger orders never cost proportionally less; register in factories | `harness/costs/model.py` + test | R3.2 |
| 9 | Default `Sizer`: weights to shares at given capital, ADV liquidity cap, minimum position, fractional rounding | `harness/engine/sizing.py` + test | R3.2, EC6 |
| 10 | Stop and gap fills: next available price, never the stop price; earnings-hold flag | `harness/engine/stops.py` + test | R3.3, EC4 |
| 11 | Event-driven backtester: signal at close t executes at next bar; takes `Executor`, `Sizer`, `CostModel`; no broker import | `harness/engine/backtest.py` + test | R3.1 |
| 12 | Runner: capital as explicit input, `cost_multiplier` run mode, seed and code-version recorded | `harness/engine/runner.py` + test | R3.5, EC6 |

### Phase 2: Data layer (data-engineer; parallel)

| # | Task | Files | Spec |
|---|---|---|---|
| 13 | Recorded fixtures: normal series, a 2-for-1 split, a dividend, two disagreeing sources, gaps, stale bars | `tests/data_fixtures/*` | R1.3 |
| 14 | Split and dividend adjustment plus sanity checks (a 2-for-1 must not look like a 50% drop) | `harness/data/adjust.py` + test | R1.3, EC1 |
| 15 | Gap and stale-bar detection | `harness/data/quality.py` + test | R1.3, EC3 |
| 16 | Reconciliation: fail the snapshot when sources disagree beyond a threshold | `harness/data/reconcile.py` + test | R1.2, R1.3, EC2 |
| 17 | Content-hash snapshot IDs, `survivorship_biased=True` always | `harness/data/snapshot.py` + test | R1.5 |
| 18 | Parquet cache with incremental fetch | `harness/data/cache.py` + test | R1.1 |
| 19 | Rate limiter and Alpaca provider (HTTP client injected; recorded responses only) | `harness/data/ratelimit.py`, `alpaca.py` + tests | R1.1 |
| 20 | Stooq and yfinance cross-checkers (reconciliation only; never the sole source) | `harness/data/crosscheck.py` + test | R1.2 |
| 21 | Index membership from change-history lists | `harness/data/membership.py` + test | R1.4 |
| 22 | Volume handling: consolidated where available, else conservative scaling | `harness/data/volume.py` + test | R1.6, EC5 |
| 23 | Regime coverage report against the owner-approved regime table | `harness/data/regimes.py` + test | R1.7 |
| 24 | Compose provider; register in factories; passes `test_data_contract.py` | `harness/data/provider.py`, `harness/testing/factories.py` | Goal |

### Phase 3: Validation and registry (validation-engineer; tasks 25 to 31 parallel with 1 and 2)

| # | Task | Files | Spec |
|---|---|---|---|
| 25 | SQLite `Registry` (append-only, `count()`, reads; `DuplicateTrialError`); passes the registry contract and the rewrite-attempt tests | `harness/registry/sqlite.py` + test | R2.6, EC9, EC11 |
| 26 | SQLite `RunLog` and `CommandInbox`; passes the runtime contract | `harness/registry/sqlite_runtime.py` + test | R2.6 |
| 27 | Purged walk-forward splitter with embargo, hypothesis tests (train ends before test, purge and embargo honored, no overlap) | `harness/validation/splitter.py` + test | R2.1 |
| 28 | Deflated Sharpe Ratio from the published formula; uses `Registry.count()` at evaluation time | `harness/validation/dsr.py` + test | R2.2, EC8 |
| 29 | PBO via CSCV | `harness/validation/pbo.py` + test | R2.2 |
| 30 | Lookahead check (truncate at t, compare signals); baseline strategy test is the template | `harness/validation/lookahead.py` + test | R2.4 |
| 31 | Floors stage: minimum trades, regime floor; tiny samples fail rather than produce a Sharpe | `harness/validation/floors.py` + test | R2.3, EC7 |
| 32 | Cost-sensitivity stage (calls runner with 2x costs; needs task 12) | `harness/validation/cost_sensitivity.py` + test | R2.3 |
| 33 | Stability stage (neighbor parameters; needs task 12) | `harness/validation/stability.py` + test | R2.3 |
| 34 | Hidden holdout outside the repo (path from environment); proposer-facing result is pass/fail or coarse only | `harness/validation/holdout.py` + test | R2.5, EC10 |
| 35 | Pipeline orchestrator: fixed stage order, stop at first failure, write a `Trial` for every candidate with `stage_reached` and outcome; no stage can be skipped or reordered | `harness/validation/pipeline.py` + test | Goal |

### Phase 4: Adversary suite (adversary; tasks 36 to 43 run as soon as Phase 0 ends, in parallel with building)

The adversary needs only `contracts/` and the pipeline's public entry point. Until task 35 lands, its tests are marked pending, not weakened.

| # | Task | Files | Spec |
|---|---|---|---|
| 36 | Seeded synthetic worlds: a planted real edge, pure noise, regime-switching series | `tests/adversarial/worlds.py` + test | R4 |
| 37 | Leaky strategy; must die at `lookahead` | `tests/adversarial/strategies/leaky.py` + test | R4 |
| 38 | Pure-noise, best of N trials; must die at `trial_adjusted` | `.../noise.py` + test | R4 |
| 39 | Cost-blind edge; must die at `cost_sensitivity` | `.../cost_blind.py` + test | R4 |
| 40 | Single-regime winner; must die at `floors` | `.../single_regime.py` + test | R4 |
| 41 | Single-parameter-peak overfit; must die at `stability` | `.../param_peak.py` + test | R4 |
| 42 | `DataView` bypass attempts (private attribute, provider leak, off-by-one shift) | `tests/adversarial/test_dataview_bypass.py` | R4 |
| 43 | Assertions: each case rejected at **exactly** its expected stage (earlier counts as failure), rejection recorded in the registry; positive control: a planted genuine edge passes the full pipeline | `tests/adversarial/test_pipeline_rejections.py` | R4, Testing |

### Phase 5: Integration and owner gate (lead; depends on everything)

| # | Task | Files | Spec |
|---|---|---|---|
| 44 | Register all real implementations in `harness/testing/factories.py`; the contract suite passes for data, costs, splitter, registry, run log and inbox | `harness/testing/factories.py` | Goal |
| 45 | `make verify-harness`; update contracts doc pointer | `Makefile`, `docs/CONTRACTS.md` | Nice to have |
| 46 | End-to-end: a registered candidate flows data, backtest, pipeline, registry on a recorded fixture; reproducibility (same seed and snapshot give identical results) | `tests/e2e/test_harness_e2e.py` | Testing |
| 47 | Clean-clone check: `uv sync --frozen && make check` plus `make verify-harness`; record results | none (verification) | Testing |
| 48 | **STOP: owner gate.** Present harness diff, threshold config, adversary results, and the Postgres status. After approval the owner adds `harness/` to `.claude/protected_paths.txt` | `.claude/protected_paths.txt` (owner) | Goal |
| 49 | (Nice to have) Postgres `Registry` validated against a live Postgres | `harness/registry/postgres.py` + test | Nice to have |

## Parallel vs Sequential

| Parallel Group | Tasks | Why |
|---|---|---|
| A: execution-modeler | 6 to 12 | owns its directories; only needs `api.py` |
| B: data-engineer | 13 to 24 | owns `harness/data/`; only needs contracts and `api.py` |
| C: validation-engineer (stats and registry) | 25 to 31, 34 | independent of the backtester |
| D: adversary | 36 to 42 | needs only contracts and the pipeline entry point |

| Sequential | Depends On | Why |
|---|---|---|
| Phase 0 (1 to 5) | none | locks, dependencies and shared types must exist before anyone writes code |
| 11 | 8, 9, 10 | the loop wires cost, sizing and stops |
| 12 | 11 | the runner wraps the loop |
| 32, 33 | 12 | stages call the runner |
| 35 | 25, 27 to 34 | the orchestrator needs every stage |
| 43 | 35, 36 to 42 | assertions run the real pipeline |
| 44 | 8, 24, 25, 26, 27 | register what exists |
| 46, 47 | all | end-to-end and clean-clone checks |
| 48 | all | owner review |

## Testing Plan

| Layer | Tests | Spec link |
|---|---|---|
| Unit | Metrics known values; cost monotonicity (hypothesis); sizing under capital changes; DSR and PBO against published reference values; splitter properties (hypothesis) | R2, R3, EC6, EC7, EC8 |
| Data | Split not a crash; disagreeing sources fail the snapshot; gaps and stale bars; volume scaling; snapshot hash stability. Recorded fixtures only, no network | R1, EC1 to EC3, EC5 |
| Registry | Contract suite for registry, run log, inbox; INSERT OR REPLACE, upsert, UPDATE and DELETE blocked; duplicate id | R2.6, EC9, EC11 |
| Stages | Lookahead catches a known leak and passes the baseline; floors fail small samples; cost doubling; stability | R2, EC7 |
| Adversary | Six cases rejected at exactly the expected stage and recorded; positive control passes | R4 |
| Contract suite | Every real implementation added to the shared fixtures passes | Goal |
| E2E | One candidate through data, backtest, pipeline, registry; reproducible runs | Goal |
| Holdout | Proposer-facing output contains no detailed holdout metrics | R2.5, EC10 |
| Manual (owner) | Harness diff review; threshold approval; Postgres status | Gate |

## Risks and Unknowns

- **Hooks may not reach teammates.** Unverified until Task 1. If they do not, protection falls back to worktrees plus prompt-level rules, which is weaker; the owner is told.
- **Thresholds are unvalidated.** The regime and minimum-trade floors may be unreachable with about 9 years of data. If the positive control cannot pass, that is a finding about the thresholds, and the owner decides. Nobody retunes them to pass.
- **Survivorship bias cannot be removed**, only tagged and haircut. Membership reconstruction from free lists is approximate.
- **DSR/PBO correctness is hard to eyeball.** Tests must reproduce published numerical examples; the owner may want an independent check. Licences must be verified before using any statistics library.
- **Free-data terms and rate limits change.** Verify Alpaca, Stooq and yfinance terms before relying on them.
- **Adversary and builders can drift.** The adversary may not edit harness code, and builders may not edit adversary tests. The owner reviews both diffs at the gate.
- **Postgres DDL has never run.** Task 49 stays optional until a live Postgres is available.
- **CI cannot be verified locally**, as in Phase 0.

## Gate 2 Checklist

**Architecture**
- [x] Follows the layering rules (harness imports only contracts and fakes; no broker, risk, strategies or proposer)
- [x] Each layer only calls the layer below it
- [x] Components are in the right directories, ownership assigned per directory

**Task Breakdown**
- [x] All files to change and all new files are listed
- [x] Tasks are small (at most 3 files plus tests)
- [x] Dependencies clear; parallel vs sequential marked
- [x] Conflicts with the Phase 0 locks identified and routed to the owner

**Testing**
- [x] Data layer, business logic, integration and edge cases from the spec all mapped
- [x] UI tests: not applicable (no UI)
- [x] Every spec requirement R1 to R6 and edge cases EC1 to EC11 trace to at least one task

## Spec traceability

R1 → 13 to 24 · R2 → 25 to 35 · R3 → 6 to 12 · R4 → 36 to 43 · R5 → 4, 5 · R6 → 2 · EC1 → 14 · EC2 → 16 · EC3 → 15 · EC4 → 10 · EC5 → 22 · EC6 → 9, 12 · EC7 → 31 · EC8 → 28 · EC9 → 25 · EC10 → 34 · EC11 → 25
