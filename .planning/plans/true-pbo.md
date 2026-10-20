# Plan: True PBO

**Spec**: .planning/specs/true-pbo.md
**Epic**: none
**Created**: 2026-10-20
**Status**: BUILT 2026-10-20 by a separate unlocked session; this plan was written first and the build differs from it as listed below. Read the code and `docs/FOLLOWUPS.md` for the truth; the rest of this file is the original intent.

**As built, differences from this plan**
- No dedupe: owner decision 2026-10-20 is that re-submitted identical code counts as an extra peer, so there is no `dedupe_key` anywhere (the plan's tasks and component rows about it do not apply).
- `pbo_min_trials = 30` (plan said 20), `pbo_max_peers = 200`, `pbo_n_blocks = 16`, with validators (even blocks; the gate must be reachable).
- Group key and peer matrix live in `harness/validation/peers.py` (plan: `pbo_groups.py`). The group key excludes the symbol list on purpose.
- The existing `contracts/sql/registry_sqlite.sql` was edited to `IF NOT EXISTS` throughout and gained `trial_returns` plus its triggers (plan: a separate returns DDL file). The existing tables are unchanged.
- Payload encoding is zlib of int64 epoch-microsecond deltas then float32 returns (plan: int32 day numbers).
- The stage change is in `harness/validation/dsr.py` (`trial_adjusted_stage`): `peer_returns` gates, `neighbour_returns` is recorded as `pbo_neighbours` and never gates.
- Not yet done: commit; review of why pure-noise PBO was 0.99 at 21 columns.

Stack: Python. Layers: `contracts/` (frozen leaf package), `fakes/`, `harness/registry` (SQLite),
`harness/validation` (pipeline stages). `harness` may not import strategies, proposer, broker, risk or
engine.

## What reading the code changed

1. **The DDL is frozen in `contracts/sql/`.** Both `registry_sqlite.sql` and `registry_postgres.sql` live
   in the protected `contracts/` package, and `harness/registry/_db.py` applies that file only when the
   `trials` table is missing. So the new table cannot just be created in `harness/`. Plan: add one new
   file `contracts/sql/registry_returns_sqlite.sql` (and a Postgres twin), leave the existing DDL
   untouched, and have `connect()` apply the returns file with `CREATE TABLE IF NOT EXISTS` so existing
   database files gain the table without a migration of `trials`.
2. **The pipeline already passes a matrix to the trial-adjusted stage.** `trial_adjusted_stage(base,
   registry, trial_returns=runs.matrix(), n_trials=registry.count() + 1, ...)` currently gets the
   candidate plus its neighbours. The change is to build that matrix from registry peers instead and keep
   the neighbour matrix only for the informational `pbo_neighbours` metric.
3. **Dedupe is cheapest done in storage.** The owner decided identical code counts once. I store a
   `dedupe_key` (hash of `code_hash` and canonical params) next to each series so the read can return
   the earliest series per key in one query, without a registry lookup per peer.
4. **Runtime is not a constraint** (measured 2026-10-20, 2,200 rows): 16 blocks (12,870 combinations)
   takes 0.03 s at 20 peers, 0.10 s at 100, 0.18 s at 200, 0.38 s at 400. At 400 peers the intermediate
   arrays are about 330 MB. So `pbo_max_peers = 200` and `pbo_n_blocks = 16` stand. 20 blocks (184,756
   combinations) would need several GB at large N, so do not raise the block count.

## Components

| Component | Type | Purpose | Spec |
|---|---|---|---|
| `ReturnSeries` | frozen pydantic model | One trial's dated daily returns plus `group_key` and `dedupe_key` | R1, R3 |
| `Registry.append_returns`, `returns_for_group` | protocol methods | Write once, read peers (deduplicated, bounded) | R1, edge 4 |
| `trial_returns` table + triggers | DDL | Append-only storage, no UPDATE or DELETE | R2 |
| returns codec | pure functions | int32 day numbers + float32 returns, zlib | R1 |
| `group_key` / `dedupe_key` helpers | pure functions | Stable hashes | R3, edge 4 |
| Pipeline wiring | stage change | Store candidate series, fetch peers, run CSCV, gate | R4, R5 |
| Thresholds | config | `pbo_min_trials`, `pbo_max_peers`, `pbo_n_blocks` | R6 |

## Files

### New

| File | Location | Protected? |
|---|---|---|
| `registry_returns_sqlite.sql` | `contracts/sql/` | yes |
| `registry_returns_postgres.sql` | `contracts/sql/` | yes |
| `returns_codec.py` | `harness/registry/` | no |
| `pbo_groups.py` (group and dedupe keys) | `harness/validation/` | no |
| `test_returns_codec.py`, `test_pbo_groups.py` | `tests/harness/...` | no |
| `test_pbo_true.py` (adversary) | `tests/adversarial/` | no |

### Changed

| File | What changes | Protected? |
|---|---|---|
| `contracts/registry.py` | `ReturnSeries`, two Protocol methods | **yes** |
| `tests/contract_suite/test_registry_contract.py` | contract tests for the new methods | **yes** |
| `fakes/registry.py` | in-memory implementation | no per hook list, but part of the contract surface |
| `harness/registry/_db.py` | apply the returns DDL idempotently | no |
| `harness/registry/sqlite.py` | implement the two methods | no |
| `harness/validation/pipeline.py` | store series, build peer matrix, pass `pbo_neighbours` | no |
| `harness/validation/` trial-adjusted stage (where `pbo_gating` is set) | gate when enough peers | no |
| `harness/thresholds.py`, `tests/harness/test_thresholds.py` | three thresholds | no |
| `tests/harness/registry/`, `tests/harness/validation/test_pipeline.py` | tests | no |
| `docs/FOLLOWUPS.md`, `.planning/specs/true-pbo.md` | status | no |

## Tasks (one commit each, at most 3 files, tests first)

### Phase A: contract surface (requires the unlocked session; do these first and stop)

| # | Task | Files |
|---|---|---|
| A1 | `ReturnSeries` model and the two Protocol methods | `contracts/registry.py` |
| A2 | Returns DDL, SQLite and Postgres, with BEFORE UPDATE, DELETE and a BEFORE INSERT duplicate guard (matching the `trials` pattern) | `contracts/sql/registry_returns_sqlite.sql`, `contracts/sql/registry_returns_postgres.sql` |
| A3 | Contract tests for every Registry implementation: append once, duplicate refused, no update or delete, group isolation, ordering and `limit`, `exclude`, one series per dedupe key | `tests/contract_suite/test_registry_contract.py` |
| A4 | In-memory fake | `fakes/registry.py` |

After A4, relaunch normally. Everything below is unprotected.

### Phase B: harness (depends on A)

| # | Task | Files |
|---|---|---|
| B1 | Codec: float32 round trip within tolerance, lossless timestamps, payload size at 1,000 and 2,500 days | `harness/registry/returns_codec.py`, `tests/harness/registry/test_returns_codec.py` |
| B2 | `group_key` and `dedupe_key` (stable, order-independent params, snapshot and window and cost and capital sensitivity) | `harness/validation/pbo_groups.py`, `tests/harness/validation/test_pbo_groups.py` |
| B3 | SQLite implementation and idempotent DDL on connect (an old database file gains the table; the raw UPDATE and DELETE are aborted) | `harness/registry/_db.py`, `harness/registry/sqlite.py`, `tests/harness/registry/test_sqlite_returns.py` |
| B4 | Thresholds `pbo_min_trials=20`, `pbo_max_peers=200`, `pbo_n_blocks=16` | `harness/thresholds.py`, `tests/harness/test_thresholds.py` |

### Phase C: pipeline (depends on B)

| # | Task | Files |
|---|---|---|
| C1 | Store the candidate's series; fetch up to `pbo_max_peers` earlier deduplicated peers (excluding the candidate's own key); refuse a series whose timestamps differ from the group's (edge 2) | `harness/validation/pipeline.py`, `tests/harness/validation/test_pipeline.py` |
| C2 | CSCV over candidate plus peers; gate when the deduplicated group (candidate included) has at least `pbo_min_trials`, else `pbo_gating = 0.0` with the reason; keep the neighbour number as `pbo_neighbours`; handle edges 1, 3, 5, 6 | trial-adjusted stage file(s), its test file |
| C3 | Adversary tests: best-of-N noise rejected with high PBO; a planted edge among noise passes with low PBO; stable across seeds; duplicates counted once | `tests/adversarial/test_pbo_true.py` |

### Parallel and sequential

| Parallel group | Tasks | Why |
|---|---|---|
| P1 | B1, B2, B4 | independent files |
| Sequential | A1, then A2 and A3 and A4 (A2, A4 parallel after A1), then B3 (needs A1, A2, B1), then C1, then C2, then C3 | each needs the previous layer |

## Testing plan

| Spec item | Test | Task |
|---|---|---|
| R1 | contract tests run against the fake and SQLite; float32 round trip | A3, B1, B3 |
| R2 | raw UPDATE, DELETE and INSERT OR REPLACE aborted | A3, B3 |
| R3 | group isolation; differing timestamps refused | A3, C1 |
| R4, R5 | gates at `pbo_min_trials`, informational below; candidate excluded from its own peers | C1, C2 |
| Decision 3 | identical code counts once; all trials still log and count for DSR | A3, C2, C3 |
| Edge 3 | zero-variance series | C2 |
| Edge 5 | fewer rows than blocks: not computed, recorded | C2 |
| Edge 6 | series write fails after the trial row is written: trial excluded from later PBOs, surfaced as a metric | C1 |
| Goal | best-of-N noise vs planted edge | C3 |

Quality gates before each commit: `uv run pytest`, `ruff check`, `ruff format --check`, `mypy harness scripts`, `lint-imports`.

## Risks

- **Selection inside PBO.** Peers are whatever earlier trials were run on the same data. If the proposer
  only ever submits strong-looking variants of one idea, the peer set is narrow and PBO is optimistic.
  This is inherent; the lifetime holdout cap and DSR remain the other defences.
- **Frozen schema change.** `contracts/sql/` is frozen. The plan adds new files instead of editing the
  existing DDL, so the `trials` table and its tests are untouched.
- **Series that are not comparable.** Group key fixes this by construction; the writer refuses
  mismatched timestamps. A calendar library update that changes sessions would put new trials in a
  different group on purpose (the key includes the window, and the snapshot id changes with the data).
- **Ordering of the Phase 1 lock.** This work edits `harness/`; do it before the lock, or unlock again.

## Gate 2 check

- Architecture: contract, DDL, fake, SQLite, pipeline layers in dependency order; no harness import of
  forbidden packages. Pass.
- Task breakdown: all files listed, protected ones marked, every task at most 3 files, dependencies and
  parallel groups marked. Pass.
- Testing: contract, storage, pipeline and adversary tests planned; every spec edge case maps to a test.
  Pass.
