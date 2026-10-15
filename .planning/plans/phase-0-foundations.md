# Plan: Phase 0 Foundations

**Spec**: .planning/specs/phase-0-foundations.md
**Epic**: none
**Created**: 2026-10-15
**Status**: draft

## Stack and starting point

Greenfield Python (no Kotlin or Next.js files, so no existing code patterns to match; the "pattern" is the layering in `CLAUDE.md`). Findings from the environment check:

- System Python is 3.9.6, too old. `uv` (0.12.5) is installed and will fetch and pin **Python 3.12**; this needs network access once.
- Not installed: `just`, `gitleaks`, `pre-commit`. Plan uses **Makefile** instead of `just`, **detect-secrets** (pure Python) instead of gitleaks, and `pre-commit` as a uv dev dependency.
- Git repo has **no commits yet** and the remote is `github.com/Nishi46/Fin_research`. `gh` is **not logged in**, so I cannot check repo visibility, branch protection, or CI from here. Those are owner steps (documented in `docs/PROTECTIONS.md`).

Layering for this phase (mirrors the architecture rules, adapted to Python):

```
contracts/   leaf package: types + Protocols. Imports nothing else in the repo.
fakes/       in-memory implementations of every contract (test/dev only)
engine/      host-agnostic skeleton: env config + run_once wiring. Depends on contracts (+ fakes in tests).
strategies/  sandbox. May import contracts only.
harness/ risk/ broker/ proposer/   empty packages with ownership READMEs (filled in later phases)
```

## Components

| Component | Type | Purpose |
|---|---|---|
| `CapitalConfig` | contract type | Explicit capital input; default 10,000 |
| `Intent` | contract type | Long-only target weights, validated |
| `DataView` | contract (the one concrete class) | Guards data access to `<= asof`; raises otherwise |
| `DataProvider`, `Strategy`, `CostModel`, `Splitter`, `Registry`, `RunLog`, `CommandInbox` | Protocols | Frozen interfaces between components |
| Registry DDL | SQL | Append-only `trials`, `runs`, `commands`, `command_status_history` (Postgres + SQLite) |
| In-memory fakes | test doubles | One per Protocol; pass the shared contract suite |
| Engine skeleton | orchestration | env-only config, `run_once` through fakes |
| Import-linter contracts | guard | Enforce package boundaries |
| Capital-literal check, secret scan | guards | Fail CI on hard-coded account size or secrets |
| Path-protection hook | guard | Blocks agent writes to protected paths |

`DataView` is the only concrete behavior in `contracts/`, deliberate: the asof guard is the contract (spec edge case 1) and Phase 1 must not reimplement it differently.

## File Locations

| File | Location | Purpose |
|---|---|---|
| `pyproject.toml`, `.python-version`, `uv.lock` | repo root | deps, ruff, mypy, pytest, import-linter config |
| `.gitignore`, `.env.example` | repo root | ignore `.env`; document env vars |
| `.pre-commit-config.yaml`, `.secrets.baseline` | repo root | ruff, mypy, detect-secrets hooks |
| `Makefile` | repo root | `make check` = lint + types + tests + guards |
| `contracts/*.py` | `contracts/` | capital, intent, dataview, data, strategy, costs, splitting, registry, runtime |
| `contracts/sql/registry_postgres.sql`, `registry_sqlite.sql` | `contracts/sql/` | frozen registry DDL |
| `fakes/*.py` | `fakes/` | in-memory implementations |
| `strategies/baseline.py` | `strategies/` | trivial strategy to prove wiring |
| `engine/config.py`, `engine/run.py` | `engine/` | env config, run_once |
| `tools/check_capital_literals.py` | `tools/` | account-size literal check |
| `tests/contract_suite/`, `tests/guards/`, `tests/unit/`, `tests/e2e/` | `tests/` | all tests |
| `.claude/settings.json`, `.claude/hooks/protect_paths.py` | `.claude/` | path-protection rules + hook |
| `.github/workflows/ci.yml`, `.github/CODEOWNERS` | `.github/` | CI and review ownership |
| `docs/CONTRACTS.md`, `docs/PROTECTIONS.md`, `docs/PHASE1_TASKS.md` | `docs/` | teammate-facing docs and owner gate pack |

## Files to Change

| File | What Changes | Why |
|---|---|---|
| `docs/project.md`, `CLAUDE.md` | Only if the build changes a decision (e.g. detect-secrets, Makefile, protection limits discovered) | keep docs honest |

## Tasks

Scaffold tasks (T3) are mechanical and exceed the 3-file guideline (empty `__init__.py` files); everything with logic stays within 3 files. Every logic task is **test first**: write the failing test, then the code.

### Phase 1: Tooling base (sequential)

| # | Task | Files | Spec |
|---|---|---|---|
| 1 | `uv init`-style project: pin Python 3.12, deps (pydantic, pytest, hypothesis, ruff, mypy, import-linter, pre-commit, detect-secrets), ruff + mypy (strict) + pytest config; generate lock | `pyproject.toml`, `.python-version`, `uv.lock` | R2, E8 |
| 2 | `.gitignore` (incl. `.env`, `.venv`, data caches), `.env.example` (paper endpoint default, DB URL placeholder, no values) | `.gitignore`, `.env.example` | R9, E7 |
| 3 | Package scaffold with ownership READMEs for `harness/ risk/ broker/ strategies/ proposer/ contracts/ fakes/ engine/ tools/ web/` (web is README-only) and empty `tests/` dirs | ~25 mechanical files | R1 |
| 4 | `Makefile` with `check`, `test`, `lint`, `types`, `guards` targets; verify `make check` runs green on the empty scaffold | `Makefile` | R2, nice-to-have |

### Phase 2: Contracts, tests first (depends on Phase 1)

| # | Task | Files | Spec |
|---|---|---|---|
| 5 | `CapitalConfig`: tests (default 10,000, rejects <= 0, UTC-aware `effective_at`, immutable), then type | `contracts/capital.py`, `tests/unit/test_capital.py` | R5 |
| 6 | `Intent`: tests (rejects negative, > 1, sum > 1, NaN/inf, empty ok), then type | `contracts/intent.py`, `tests/unit/test_intent.py` | R5, edge |
| 7 | `DataView` + data types (`Bars`, `Snapshot`, `AdjustmentFactors`, `Membership`) and `DataProvider` Protocol: tests incl. **peeking strategy raises past `asof`**, hypothesis property on random asof | `contracts/data.py`, `contracts/dataview.py`, `tests/unit/test_dataview.py` | R5, E1 |
| 8 | `Strategy` Protocol and `Order`/`MarketState` types; `CostModel` and `Splitter` Protocols (`Split` with purge/embargo params) | `contracts/strategy.py`, `contracts/costs.py`, `contracts/splitting.py` | R5 |
| 9 | `Registry` Protocol + `Trial` model; test asserts the Protocol exposes no update/delete | `contracts/registry.py`, `tests/unit/test_registry_protocol.py` | R5, R6, edge |
| 10 | `RunLog`, `CommandInbox` Protocols, `Command` and status-history types | `contracts/runtime.py`, `tests/unit/test_runtime_types.py` | R5 |
| 11 | Public exports and teammate doc: `contracts/__init__.py`, `docs/CONTRACTS.md` (each contract, its invariants, change process) | `contracts/__init__.py`, `docs/CONTRACTS.md` | R5, R8 |
| 12 | Registry DDL (Postgres + SQLite), UUID ids, TEXT, TIMESTAMPTZ/UTC, append-only **triggers raising on UPDATE/DELETE**. Test: load SQLite DDL, insert works, update and delete fail. Postgres DDL only syntax-checked (no live PG yet) | `contracts/sql/registry_postgres.sql`, `contracts/sql/registry_sqlite.sql`, `tests/unit/test_registry_ddl.py` | R6 |

### Phase 3: Fakes and shared contract suite (depends on Phase 2)

| # | Task | Files | Spec |
|---|---|---|---|
| 13 | Shared contract tests, parameterized by fake factory, reusable by Phase 1 real implementations: data, registry (append-only behavior), runtime | `tests/contract_suite/test_data_contract.py`, `test_registry_contract.py`, `test_runtime_contract.py` | R7 |
| 14 | In-memory fakes: `DataProvider` (synthetic bars, content-hash snapshot id, `survivorship_biased=True`), `Registry`, `RunLog`, `CommandInbox` | `fakes/data.py`, `fakes/registry.py`, `fakes/runtime.py` | R7 |
| 15 | Fakes for `CostModel` and `Splitter`; baseline strategy (equal-weight top-N by 5-day return, emits `Intent` only); tests | `fakes/costs.py`, `fakes/splitting.py`, `strategies/baseline.py` | R7 |

### Phase 4: Engine skeleton (depends on Phase 3)

| # | Task | Files | Spec |
|---|---|---|---|
| 16 | Env-only config: paper endpoint default, `CapitalConfig` from env/default, no paths; test that no absolute paths or home-dir reads occur | `engine/config.py`, `tests/unit/test_engine_config.py` | R9, E6 |
| 17 | `run_once(config, provider, strategy, runlog)`: data_view → strategy → intent → append run record; no sizing, broker or orders. E2E test from a clean temp dir, env only | `engine/run.py`, `tests/e2e/test_run_once.py` | R7, E6 |

### Phase 5: Guards, CI and protections (depends on Phases 1 to 4; groups parallel)

| # | Task | Files | Spec |
|---|---|---|---|
| 18 | Import-linter contracts: `contracts` is a leaf; `strategies` may import only `contracts`; `broker` importable only from `risk` and `engine`; `harness` must not import `strategies`, `proposer`, `broker`, `risk`. Test copies the repo to a temp dir, injects `strategies -> broker`, asserts `lint-imports` fails | `pyproject.toml` (importlinter section), `tests/guards/test_import_boundaries.py` | R2, E4 |
| 19 | Capital-literal check: fail on `10000`/`10_000` and similar outside `CapitalConfig` default and tests; test with a planted literal | `tools/check_capital_literals.py`, `tests/guards/test_capital_literals.py` | E5 |
| 20 | Secret scan: detect-secrets baseline + pre-commit hook; test that a planted fake key is flagged | `.pre-commit-config.yaml`, `.secrets.baseline`, `tests/guards/test_secret_scan.py` | R2, E7 |
| 21 | CI workflow: `uv sync --frozen`, ruff, mypy, pytest, lint-imports, capital check, detect-secrets; **no secrets in the workflow**; same commands as `make check` | `.github/workflows/ci.yml` | R3 |
| 22 | `CODEOWNERS` for `harness/`, `contracts/`, `risk/`, `.github/`, `pyproject.toml`, `.claude/`; `docs/PROTECTIONS.md`: branch-protection settings for the owner, contract-change process, residual gaps | `.github/CODEOWNERS`, `docs/PROTECTIONS.md` | R4, R8, E2, E3 |
| 23 | Local path protection: `PreToolUse` hook blocks Write/Edit (and best-effort Bash write patterns) on protected paths unless an owner-only unlock env var is set; `.claude/settings.json` registers the hook plus deny rules. Unit-test the hook with sample tool-call JSON (protected path blocked, allowed path passes). **Manual check:** attempt an Edit on a protected file in a live session | `.claude/hooks/protect_paths.py`, `.claude/settings.json`, `tests/guards/test_protect_paths.py` | R4, E3 |

### Phase 6: Owner gate (depends on everything)

| # | Task | Files | Spec |
|---|---|---|---|
| 24 | Phase 1 task list: per-teammate scope, owned directory, acceptance tests, which contracts each consumes, how teammates request contract changes | `docs/PHASE1_TASKS.md` | Gate |
| 25 | Full verification on a clean clone (fresh dir, `uv sync --frozen && make check`), record results; bootstrap git: commit docs on `main`, build on `feature/phase-0-foundations`, one commit per task. **Do not push** without owner OK | none (verification + git) | Testing |
| 26 | **STOP: owner review.** Present contracts, DDL, protection mechanism and limits, Phase 1 task list. Activate the lock on `contracts/`, `.github/` and `harness/` only after approval | none | Gate |

## Parallel vs Sequential

| Parallel Group | Tasks | Why |
|---|---|---|
| A | 5, 6, 8, 10 | independent contract files |
| B | 14, 15 (after 13) | independent fakes |
| C | 18, 19, 20 | independent guards |
| D | 21, 22, 23 | independent config/docs |

| Sequential | Depends On | Why |
|---|---|---|
| 1 → 2, 3, 4 | tooling | everything needs the project to exist |
| 7 | 5, 6 | `DataView` uses capital-free types but shares conventions; keep after Intent for style consistency |
| 9, 12 | 8, 10 | registry and DDL reference `Trial`/`Command` shapes |
| 13 | 7, 9, 10 | contract suite targets those Protocols |
| 16, 17 | 14, 15 | engine wires fakes |
| 21 | 18, 19, 20 | CI runs all guards |
| 25, 26 | all | verification and approval |

Phase 0 is built by the lead alone (no team), so "parallel" here means independent; work proceeds one task at a time with a commit each. Agent Teams are not used until after Task 26.

## Testing Plan

| Layer | Tests | Spec link |
|---|---|---|
| Unit (contracts) | CapitalConfig bounds and immutability; Intent rejects bad weights; DataView raises past `asof` (+ hypothesis); Registry Protocol has no update/delete; types round-trip | R5, E1 |
| DDL | SQLite: insert ok, UPDATE and DELETE blocked by trigger; Postgres: syntax check only (live test deferred to Phase 1) | R6 |
| Contract suite | Every fake passes the shared suite (reusable by Phase 1 real implementations) | R7 |
| E2E | `run_once` through fakes from a clean temp dir with env-only config | R7, E6 |
| Guards | Planted `strategies -> broker` import fails lint-imports; planted `10000` fails capital check; planted fake key fails secret scan; hook blocks protected write | E3, E4, E5, E7 |
| Tooling | Clean-clone `uv sync --frozen && make check` passes | Happy path, E8 |
| Manual | Real edit attempt on a protected path in a live session; CI run after owner pushes | E3 |

## Risks and Unknowns

- **CI cannot be verified locally.** `make check` runs the same commands, but the real workflow only runs after the owner pushes. Reported as unverified until then.
- **Branch protection** may need a paid plan for private repos; `gh` is not authenticated so I can't check. `docs/PROTECTIONS.md` records compensating controls if unavailable.
- **Local locks are bypassable by the same user** (chmod, shell). The hook stops accidental and agent-driven edits, not a determined owner; documented, not hidden.
- **Hooks and Agent Teams:** whether teammate agents inherit the same hooks/settings and can be given per-directory ownership is unverified; to be tested at the start of Phase 1.
- Python 3.12 download needs network access.

## Gate 2 Checklist

**Architecture**
- [x] Follows the layering rules (contracts leaf; strategies see only contracts; broker reachable only via risk/engine)
- [x] Each layer only calls the layer below it
- [x] Components are in the right directories

**Task Breakdown**
- [x] All files to change and all new files are listed
- [x] Tasks are small (logic tasks <= 3 files; scaffold exception noted)
- [x] Dependencies clear; parallel vs sequential marked

**Testing**
- [x] Data layer (DDL), business logic (contracts), integration (E2E), edge cases from spec all mapped
- [x] UI tests: not applicable (no UI in Phase 0)
- [x] Every spec requirement R1 to R9 and edge cases E1 to E8 trace to at least one task

## Spec traceability

R1 → T3 · R2 → T1, T18, T20 · R3 → T21 · R4 → T22, T23 · R5 → T5 to T11 · R6 → T9, T12 · R7 → T13 to T15, T17 · R8 → T11, T22 · R9 → T2, T16 · E1 → T7 · E2 → T22 · E3 → T22, T23 · E4 → T18 · E5 → T19 · E6 → T16, T17 · E7 → T20 · E8 → T1
