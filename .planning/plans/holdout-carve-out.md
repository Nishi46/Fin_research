# Plan: Holdout Carve-Out

**Spec**: .planning/specs/holdout-carve-out.md
**Epic**: none
**Created**: 2026-10-18
**Status**: draft

Stack: Python (the Kotlin/Micronaut and Next.js rules in `~/.claude` do not apply). Layers here are `harness/data` (pure data handling), `harness/validation` (the gate), `scripts/` (owner-run CLIs). `harness` may not import `strategies`, `proposer`, `broker`, `risk` or `engine` (import-linter keeps this).

## What the code review changed

Reading the code before planning corrected four things in the spec. They need your sign-off (see "Decisions needed").

1. **The raw-bars-plus-actions refactor is mostly already done.** `ComposedDataProvider` ([harness/data/provider.py](../../harness/data/provider.py)) takes raw bars and actions and adjusts at read time, point-in-time, using `end`. `ParquetCache` already stores raw bars because Alpaca is fetched with `adjustment=raw`. `ParquetCache` is not wired into anything yet. The only missing piece is somewhere to persist corporate actions. I earlier called this the biggest piece of work; it is not.
2. **The warm-up design in spec R3 is too thin.** Ten embargo sessions cannot warm up a 200-day indicator. A holdout run must therefore combine the research data (not secret, already readable) with the holdout directory, and adjust the union once at `holdout_end`. The holdout directory then holds only embargo and holdout sessions, as the spec says, but a holdout run reads both.
3. **R9 needs no registry contract change.** Each trial already records `data_snapshot_id`. A research provider built from capped data has `snapshot.end == research_end`, so the research window is recorded through the snapshot. The holdout dates live in the manifest. Adding registry fields would change a frozen contract.
4. **`HoldoutGate.evaluate(run=...)` takes a caller-supplied callable that receives the holdout path.** Whoever calls `evaluate` can read the holdout. The runner must be built by the harness (the pipeline), never by the proposer. Software cannot enforce this alone; it is part of the OS-level isolation you decided on before step 5. This plan adds a docstring warning and a test that the pipeline-owned runner is the only one used, but does not pretend to close the gap.

## Architecture

### Components

| Component | Type | Purpose | Spec |
|---|---|---|---|
| `Windows`, `compute_windows` | pure function | Compute `research_end`, `holdout_start`, `holdout_end` from NYSE sessions | R1 |
| `ResearchWindow` | `DataProvider` wrapper | Clamp every read to `research_end`; defence in depth over a provider already built from capped data | R5 |
| `split_data` | pure function | Partition bars and actions into research, embargo, holdout; enforce the R8 floors | R1, R8 |
| `HoldoutManifest` + store | pydantic model + I/O | Atomic write, content hash, load and verify, directory mode check, write-once | R2, R3, R4 |
| `ActionsStore` | small parquet store | Persist corporate actions next to the bar cache (missing today) | R7 |
| `holdout_provider` | factory | Build a `ComposedDataProvider` from research data plus holdout directory, adjusted at `holdout_end` | R6, R7 |
| `HoldoutGate` changes | existing class | Require manifest, verify hash and mode, score only returns from `holdout_start` | R6 |
| `scripts/carve_holdout.py` | owner CLI | Gate on snapshot quality and reconciliation, then split and write | R2, R8 |

### New files

| File | Location | Purpose |
|---|---|---|
| `window.py` | `harness/data/` | `Windows`, `compute_windows`, `ResearchWindow` |
| `split.py` | `harness/data/` | `split_data` and R8 floor checks |
| `holdout_store.py` | `harness/data/` | Manifest model, atomic write, load/verify, mode check, `holdout_provider` |
| `carve_holdout.py` | `scripts/` | Owner-run CLI (`--dry-run`, `--symbols`, `--years`, `--prune-cache`) |
| `test_window.py` | `tests/harness/data/` | Windows and `ResearchWindow` leak tests |
| `test_split.py` | `tests/harness/data/` | Partition properties, R8 refusals |
| `test_holdout_store.py` | `tests/harness/data/` | Atomic write, tamper, crash, re-carve, modes |
| `test_carve_script.py` | `tests/harness/data/` | CLI with injected fetchers, no network |
| `test_holdout_carve_e2e.py` | `tests/e2e/` | Synthetic carve to research pipeline to gate, plus repo leak scan |

### Files to change

| File | What changes | Why |
|---|---|---|
| `harness/thresholds.py` | Add `holdout_embargo_sessions=10`, `holdout_min_session_fraction=0.9`, `holdout_min_research_years=3` | Frozen rules live here (R1, R8) |
| `tests/harness/test_thresholds.py` | Assert the new values and bounds | Thresholds are tested |
| `harness/data/cache.py` | Add `ActionsStore` (one parquet file of actions) | Actions are not persisted today (R7) |
| `harness/validation/holdout.py` | Manifest-required gate; verify hash and mode; score returns from `holdout_start` only; docstring on who builds the runner | R4, R6 |
| `tests/harness/validation/test_holdout.py` | Existing tests build their directory through a helper that writes a valid manifest; new tests for the filter, tamper, missing manifest | R6 |
| `docs/FOLLOWUPS.md` | Mark carve built; list the runner-construction and isolation preconditions | Tracking |
| `.planning/specs/holdout-carve-out.md` | Amend R3 (warm-up), R7 (already mostly done), R9 (no contract change) | Keep spec truthful |

## Tasks

Each task is one commit, at most 3 files, tests first.

### Phase 1: Pure core (no I/O)

| # | Task | Files |
|---|---|---|
| 1 | Add the three thresholds with bounds and tests (R1, R8) | `harness/thresholds.py`, `tests/harness/test_thresholds.py` |
| 2 | `compute_windows` on NYSE sessions: expected dates, exactly `embargo_sessions` sessions between `research_end` and `holdout_start`, boundary never splits a session, last-session edge (R1, edge 10) | `harness/data/window.py`, `tests/harness/data/test_window.py` |
| 3 | `ResearchWindow` wrapper: clamps `bars`, `corporate_actions`, `snapshot` end and `membership`; parametrized leak test over every `DataProvider` entry point so a new one cannot be added uncovered (R5) | `harness/data/window.py`, `tests/harness/data/test_window.py` |
| 4 | `split_data`: partition by session into research, embargo, holdout; Hypothesis property that the union equals the input with no overlap; R8 refusals (research too short, holdout too short, unordered or duplicate bars); IPO and delisting symbols (edges 4, 7) | `harness/data/split.py`, `tests/harness/data/test_split.py` |

### Phase 2: Store (depends on 2 and 4)

| # | Task | Files |
|---|---|---|
| 5 | `ActionsStore` parquet round-trip beside the bar cache; empty and missing cases | `harness/data/cache.py`, `tests/harness/data/test_cache.py` |
| 6 | `HoldoutManifest` (pydantic, frozen), content hash over bars and actions files, atomic write (temp dir, verify hash, rename, manifest last, exclusive create), load and verify, directory mode 0700 check (R2 to R4, edges 1, 2, 9) | `harness/data/holdout_store.py`, `tests/harness/data/test_holdout_store.py` |
| 7 | Failure-mode tests: re-carve refuses and leaves bytes identical; crash before manifest leaves a directory the gate refuses; tampered date or file detected; loose mode refused; path inside repo refused | `tests/harness/data/test_holdout_store.py` |
| 8 | `holdout_provider(research_bars, research_actions, holdout_dir)`: union adjusted once at `holdout_end`; test that a holdout-date split is not a crash and that research reads never see holdout-window actions (R7, edge 5) | `harness/data/holdout_store.py`, `tests/harness/data/test_holdout_store.py` |

### Phase 3: Gate (depends on 6)

| # | Task | Files |
|---|---|---|
| 9 | `HoldoutGate`: require manifest, verify hash and mode, score only returns on or after `holdout_start`; embargo-period edge planted huge does not move the verdict; helper so old tests write a valid manifest (R6, edge 8) | `harness/validation/holdout.py`, `tests/harness/validation/test_holdout.py` |

### Phase 4: Owner script (depends on 4, 6, 8)

| # | Task | Files |
|---|---|---|
| 10 | `carve_holdout.py`: fetch via the Alpaca client, build a strict `ComposedDataProvider` with the yfinance checker so `snapshot()` raises on quality or reconciliation failure, compute windows, `--dry-run` prints windows and per-side counts, refuse if a manifest exists, refuse stale cache files unless `--prune-cache`, `--allow-stale` for an old last session (R2, R8, edges 3, 6) | `scripts/carve_holdout.py`, `tests/harness/data/test_carve_script.py` |

### Phase 5: Verification and docs (depends on all)

| # | Task | Files |
|---|---|---|
| 11 | End-to-end on a synthetic snapshot: carve, run the research pipeline through `ResearchWindow`, run the gate on a planted-edge fixture and on noise; scan the repo tree for holdout-side bars | `tests/e2e/test_holdout_carve_e2e.py` |
| 12 | Amend the spec (R3, R7, R9) and update FOLLOWUPS | `.planning/specs/holdout-carve-out.md`, `docs/FOLLOWUPS.md` |

### Parallel and sequential

| Parallel group | Tasks | Why |
|---|---|---|
| A | 1, 2, 5 | Independent files |
| B | 3, 4 | Both only need task 2's `Windows` type; separate files and tests (3 shares `window.py` with 2, so run after 2) |

| Sequential | Depends on | Why |
|---|---|---|
| 3, 4 | 2 | Use `Windows` |
| 6 | 4, 5 | Writes what `split_data` produces and what `ActionsStore` reads |
| 7, 8 | 6 | Extend the store |
| 9 | 6 | Gate reads the manifest |
| 10 | 4, 6, 8 | Calls all of them |
| 11 | 3, 9, 10 | Exercises the whole path |
| 12 | 11 | Document what was actually built |

## Testing plan

Each test maps to a spec requirement or edge case. The leak tests are the point of the feature, so they are written first in their tasks.

| Spec item | Test | Task |
|---|---|---|
| R1, edge 10 | Windows on a synthetic calendar; no session split; exact embargo count | 2 |
| R5 | Every `DataProvider` method asked past `research_end` returns nothing or raises; a stale full cache cannot leak | 3, 11 |
| R1, R8, edges 4, 7 | Partition property (Hypothesis), IPO and delisting, short research, short holdout, unordered or duplicated bars | 4 |
| R3, R4, edges 1, 2, 9 | Re-carve refuses, crash leaves refusable directory, tamper detected, loose mode refused | 6, 7 |
| R7, edge 5 | Split on a holdout date: research prices unchanged, holdout returns show no crash | 8 |
| R6, edge 8 | Edge planted in the embargo does not move the verdict; no manifest means refuse; planted edge in holdout passes, noise fails | 9 |
| R2, R8, edges 3, 6 | CLI with injected fetchers: dry run writes nothing, quality or reconcile failure aborts before any write, stale cache refused without `--prune-cache` | 10 |
| Goal | No holdout-side bar anywhere under the repo root after a carve | 11 |

Quality gates before each commit: `uv run pytest`, `ruff check`, `ruff format --check`, `mypy harness scripts`, `lint-imports`.

## Decisions needed (Gate 2)

1. **Three new thresholds** (the frozen config is yours to set). Proposed: `holdout_embargo_sessions = 10` (you already approved), `holdout_min_session_fraction = 0.9` (the holdout must have at least 90% of the expected sessions), `holdout_min_research_years = 3` (research must keep at least 3 years). With history starting around 2016, the research side will have about 8 years, so neither floor binds in practice.
2. **Spec amendments** for R3, R7 and R9 as listed under "What the code review changed". None weakens an invariant.
3. **Universe for the carve.** The script takes `--symbols`. Choosing the real universe (large caps and ETFs, membership handling) is a separate decision this plan does not make. The carve is irreversible, so the symbol list should be settled before the real run.

## Risks

- **Runner construction** (item 4 above) is not closed by this plan. Treat the OS-level isolation as a real precondition for step 5, not a nice-to-have.
- **`exchange_calendars` coverage.** `trading_sessions` clips to the library's range; a last session near the end of that range must not silently shorten the holdout. Task 2 tests the edge, and R8's length floor is the backstop.
- **First real carve uses Alpaca's IEX feed.** Closes carry the IEX-vs-consolidated noise seen in the reconciliation. It does not leak, but it is the data the holdout is graded on, so a pass there inherits that noise.
- **Ordering.** Tasks 1 to 9 can be built and tested entirely on synthetic data. The real carve (task 10 run on real data) happens only after you approve the symbol list.

## Gate 2 check

- Architecture: layers respect import-linter contracts; pure core separated from I/O; scripts are owner-run only. Pass.
- Task breakdown: all new and changed files listed; every task at most 3 files; dependencies and parallel groups marked. Pass.
- Testing: store, logic, gate and CLI tests planned; every spec edge case maps to a test; no API or UI layer exists. Pass.
