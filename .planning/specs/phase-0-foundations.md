# Spec: Phase 0 Foundations

**Created**: 2026-10-15
**Status**: draft
**Author**: Nishi Kantamneni
**Epic**: none (first phase of the build in `docs/project.md` §11 and the agent-team plan)

## Problem

Phase 1 is a team of four agents building the harness in parallel. Without frozen interfaces and a locked-down repo, they would build against guesses, and nothing would stop an agent from editing the code that grades strategies. The project's core invariant (the AI must not edit what grades it) has to be enforced by the repo itself, not by instructions alone.

## Goal

A repo scaffold, tooling, CI, protections and frozen contracts such that, at the owner's approval gate:
- all Phase 1 teammates can build and test their pieces independently against the same contracts;
- CI fails if `strategies/` imports `broker/` or harness internals, or if a secret or a hard-coded account size is committed;
- a write to `harness/` or `.github/` by an unauthorized actor is actually blocked, verified by test, with its limits documented;
- the engine skeleton runs end to end on in-memory fakes from a clean directory using only environment config, so a later move from the owner's machine to GitHub Actions needs configuration only.

## User Stories

- As the **owner and reviewer**, I want the interfaces and protections approved once, up front, so that I review the harness against a fixed contract and never rely on an agent's promise not to touch it.
- As a **Phase 1 teammate agent** (data, validation, execution, adversary), I want a frozen contract and a directory only I own, so that I can build and test my piece in parallel without waiting on or colliding with the others.
- As the **future proposer agent**, I want a `strategies/` sandbox with one narrow interface, so that I can only emit target weights and never place orders or edit the grader.
- As the **engine** (owner's machine now, GitHub Actions later), I want state, config and secrets behind interfaces, so that changing hosts is a configuration change.

## Requirements

### Must have
1. **Repo scaffold** per the agreed layout: `harness/`, `risk/`, `broker/`, `strategies/`, `proposer/`, `web/` (placeholder only), `tests/`, `tests/adversarial/`, `docs/`. Each Python package has an `__init__.py` and a short README stating who may write there.
2. **Tooling:** Python version pinned; `uv` with a committed lockfile; `ruff` (lint + format); `mypy` strict on `harness/`, `risk/` and the contracts; `pytest` + `hypothesis`; `import-linter`; `pre-commit` (ruff, mypy, secret scan).
3. **CI** (GitHub Actions workflow, no broker or database secrets): lint, type-check, tests, import-linter, secret scan. Runs on every PR.
4. **Protections:**
   - `CODEOWNERS` covering `harness/`, the contracts package, `risk/`, `.github/`, and `pyproject.toml` import-linter config.
   - Documented branch-protection settings the owner applies on GitHub (require PR, require CODEOWNER review, require CI green).
   - A local write-protection mechanism for `harness/` and `.github/` (read-only flag or Claude Code permission rules/hooks, whichever tests prove works) plus a documented list of what it cannot stop on a single-user local machine.
5. **Frozen contracts** (a `contracts` package; immutable dataclasses/pydantic models and `Protocol`s, with docstrings stating intent and invariants only, no implementation):
   - `CapitalConfig`: `amount` (default 10,000), `currency`, `effective_at`, `source`. Capital is always passed explicitly; never a global.
   - `Intent`: `asof`, mapping symbol → target weight. Long-only, each weight in [0, 1], sum ≤ 1. No shares, dollars or order fields.
   - `Strategy` Protocol: `generate(data_view, asof) -> Intent`.
   - `DataView`: exposes data only up to `asof`; any access past `asof` raises.
   - `DataProvider` Protocol: bars, corporate-action/adjustment factors, index membership as of a date; returns a content-hash snapshot ID and a `survivorship_biased` flag.
   - `CostModel` Protocol: cost as a function of order, capital and market state (size-dependent).
   - `Splitter` Protocol: yields train/validation splits with purge and embargo parameters.
   - `Registry` Protocol: append-only (`append`, read methods); no update or delete exist in the interface.
   - `RunLog` and `CommandInbox` Protocols: host-agnostic run heartbeat and website commands (capital change, promote/reject, thresholds) with append-only status history.
6. **Registry schema frozen** (DDL for Postgres and SQLite-compatible equivalent, not implemented against a live DB yet): `trials` (`id`, `created_at`, `strategy_id`, `code_hash`, `params`, `capital`, `data_snapshot_id`, `survivorship_biased`, `stage_reached`, `outcome`, `metrics`), `runs`, `commands`, `command_status_history`. UUID ids, TEXT not VARCHAR, TIMESTAMPTZ in UTC. No soft delete (append-only by design; nothing is deleted).
7. **In-memory fakes** for every Protocol, plus one trivial baseline strategy, used only to prove the pieces connect through an engine skeleton (data_view → strategy → intent) on fake data.
8. **Contract change process** documented: contract files are CODEOWNER-protected; teammates request changes through the lead; changes are owner-approved and all teammates are notified.
9. **Config and secrets** read from environment only; `.env` git-ignored; `.env.example` provided; paper endpoint is the default.

### Nice to have
- A `justfile` or `Makefile` with `check`, `test`, `lint` shortcuts.
- A short `docs/CONTRACTS.md` summarizing each contract for teammates.

### Out of scope
- Any real implementation: backtester, cost model math, walk-forward splitter, Deflated Sharpe, lookahead test logic (Phase 1).
- Data download, Alpaca connection, order code (Phases 1 and 3).
- Risk layer logic, the proposer agent, the website (Phases 2, 4, 5).
- Setting up Vercel, Neon, or the live-engine GitHub Actions workflows. Code is only kept host-agnostic.
- Choosing strategy ideas or parameters.

## Data Model

Python contract types and the registry DDL listed under Requirements 5 and 6. No live database in Phase 0. All timestamps are UTC (`Instant`-equivalent: timezone-aware `datetime` in UTC); no local timezones in contracts.

## API Changes

None. No web or HTTP API in Phase 0. The `CommandInbox` and `RunLog` are Python Protocols only.

## UI Changes

None. `web/` is a placeholder directory with a README.

## Edge Cases

1. **Interface leaks the future.** `DataView` must raise on any access past `asof`; tested with a deliberately peeking strategy.
2. **Contract frozen too early / drifting.** Contract files are CODEOWNER-protected and changed only through the lead and an owner-approved PR.
3. **Protection that isn't real.** CODEOWNERS only works with branch protection, and a local read-only lock may be bypassable by the same user. Phase 0 proves the block with a test (attempted write fails) and documents residual gaps.
4. **Import-boundary erosion.** Import-linter contracts forbid `strategies/` → `broker/`, `strategies/` → harness internals, and anything → `proposer/`. A test with a deliberate violation proves CI fails.
5. **Capital hard-coded.** A check fails CI if `10000`/`10_000` or other account-size literals appear outside `CapitalConfig` defaults and tests.
6. **Host assumptions.** The engine skeleton runs in a clean temp directory with only environment variables (no absolute paths, no persistent disk assumptions).
7. **Secrets in the repo.** Secret scan in pre-commit and CI; `.env` ignored; paper endpoint default; no keys in logs.
8. **Tooling drift.** Python and dependency versions pinned and locked, so the owner's machine, CI and later Actions agree.

## Testing Criteria

### Happy path
- `uv sync && just check` (or equivalent) passes on a clean clone: ruff, mypy strict, pytest, import-linter, secret scan.
- The baseline strategy runs through the engine skeleton on fake data and returns a valid `Intent` (long-only, weights in [0, 1], sum ≤ 1).
- Every Protocol has an in-memory fake that passes a shared contract test suite (reusable by Phase 1 implementations).

### Edge cases
- `DataView` raises when asked for data after `asof`.
- `Intent` rejects negative weights, weights > 1, sums > 1, and non-finite values.
- `Registry` fakes expose no update/delete; a test asserts the Protocol has no such methods.
- A deliberate `strategies/ → broker/` import makes import-linter fail.
- A committed fake secret makes the secret scan fail; a stray `10000` literal makes the capital check fail.
- An attempted write to a protected path is blocked (per the chosen mechanism).
- Engine skeleton runs from a clean temp directory with env-only config.

## Dependencies

- Python (version to pin in Phase 0), `uv`, `ruff`, `mypy`, `pytest`, `hypothesis`, `import-linter`, `pre-commit`, a secret-scanning tool (e.g. gitleaks or detect-secrets).
- A GitHub repository (private) with branch protection and CODEOWNERS enabled. Branch protection on private repos may need a paid plan; verify and, if unavailable, document the compensating local controls.
- Claude Code settings/hooks (`/update-config`) for local write-protection.
- Agent Teams feature enabled (owner confirmed). Exact flag to be confirmed when the first team is spawned in Phase 1.

## Gate (owner approval before Phase 1)

Phase 0 is complete when all Testing Criteria pass and the owner has reviewed and approved: the contract definitions, the registry DDL, the protection mechanism and its documented limits, and the Phase 1 task list with directory ownership. No agent team starts before this approval.

## Open items to verify during Phase 0

- Whether branch protection / required CODEOWNER review is available on the owner's GitHub plan for a private repo.
- Current free-tier limits for GitHub Actions minutes, scheduled-workflow inactivity disabling, and Neon storage (needed later, not blocking Phase 0).
- Which local write-protection mechanism actually works against agent writes (permission rules vs hooks vs file mode).
