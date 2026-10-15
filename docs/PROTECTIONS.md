# Protections

How the repo keeps the AI from editing what grades it, what each layer actually stops, and where it does not. Read the gaps; they matter more than the list of controls.

## What is enforced, and where

| Control | Where | Stops | Verified by |
|---|---|---|---|
| Import boundaries | `pyproject.toml` (import-linter), CI | `strategies/` importing the broker or harness; `contracts/` importing anything; harness importing strategies/proposer/broker/risk; broker reachable only from risk and engine | `tests/guards/test_import_boundaries.py` plants violations and asserts failure |
| Capital literal check | `tools/check_capital_literals.py`, CI, pre-commit | the default account size appearing as a literal outside `CapitalConfig` | `tests/guards/test_capital_literals.py` |
| Secret scan | detect-secrets, CI, pre-commit | committed keys and private keys | `tests/guards/test_secret_scan.py` |
| Path-protection hook | `.claude/hooks/protect_paths.py`, `.claude/settings.json`, `.claude/protected_paths.txt` | Claude Code's file tools writing to protected paths (case-insensitive, symlink-aware); a best-effort tripwire on Bash writes | `tests/guards/test_protect_paths.py` (unit, subprocess, shipped wiring and activation list), plus a manual check below. **Ships inactive** (see "The local lock") |
| Strategy sandbox tripwire | `tools/check_strategy_sandbox.py`, CI | `strategies/` importing outside a small allow-list, calling `open`/`eval`/`exec`/`getattr`/`vars`/`type` and similar, or mentioning `DataView` internals | `tests/guards/test_strategy_sandbox.py`. **Catches honest mistakes and obvious escapes only.** Trivially bypassed by determined code (for example string-built names reaching attributes through an allowed module); not a sandbox |
| CODEOWNERS + branch protection | `.github/CODEOWNERS`, GitHub settings | merging changes to protected paths without owner review | **Not verified locally.** Needs the owner to push and enable the settings below |
| Append-only registry | `contracts/sql/*.sql` | UPDATE, DELETE and replace-on-conflict (plus TRUNCATE on Postgres) on trials, runs, commands, history. Not blocked: `DROP TRIGGER`, `DROP TABLE`, or `DISABLE TRIGGER` by whoever owns the database; privileges (INSERT/SELECT only for the engine role) are the primary control | `tests/unit/test_registry_ddl.py`. SQLite is executed, including `INSERT OR REPLACE` and upsert attempts. Postgres is **pattern-checked only: never parsed or run** |
| Credentials | `.env` git-ignored; engine reads env only; paper endpoint by default; live needs `LAB_ALLOW_LIVE=1` | keys in the repo; accidental live trading | `tests/unit/test_engine_config.py`, secret scan |

## Owner steps on GitHub (not done; nothing has been pushed)

1. Create or confirm the repo is **private**.
2. Branch protection on `main` (and `develop` if used): require a pull request, require review from Code Owners, require the `check` status check, require branches to be up to date, block force pushes and deletions, include administrators.
3. **Check your plan.** Branch protection and required reviews on private repositories may need a paid GitHub plan. If unavailable, the compensating controls are: never push directly to `main`, review every diff to protected paths yourself before merging, and keep the local hook lock active.
4. **Single-account caveat.** If agents and you share one GitHub account, CODEOWNERS cannot tell them apart, and an author generally cannot approve their own pull request in a way that counts as independent review. Real separation needs agents to push through a separate account or a fine-grained token with no merge rights, with you as the only merger. Until then the control is procedural: agents never merge or push, and you read the protected-path diff in the PR before merging.
5. In CI settings, keep workflow permissions read-only and do not add repository secrets to the CI workflow.

## The local lock

The hook blocks Claude Code's Write, Edit, MultiEdit and NotebookEdit on any path listed in `.claude/protected_paths.txt` (matched case-insensitively, after resolving `..` and symlinks). For Bash it is a **tripwire, not a sandbox**: it blocks a command only if it mentions a protected path and contains a write-like word (`>`, `tee`, `touch`, `sed -i`, `rm`, `mv`, `cp`, `patch`, `rsync`, `curl -o`, `wget`, `git ... checkout/restore/reset/clean`, or `python`/`node`/`perl`/`ruby` with `-c`/`-e`/a heredoc). It fails closed: malformed input, an unexpected error, or a missing `protected_paths.txt` all block the call.

It ships **inactive**: the list is empty so the lead can finish building Phase 0. It is activated at the gates:

- **Phase 0 gate (after owner approval):** add `contracts/`, `.github/`, `.claude/`, `pyproject.toml`, `tools/`, `engine/`, `tests/contract_suite/`, `tests/guards/`, `docs/PROTECTIONS.md`. The exact list is in `.claude/protected_paths.txt` as comments, and a test checks it covers the guards.
- **Phase 1 gate (after the harness review):** add `harness/`.

To work on a locked path deliberately, the owner launches Claude Code with `LAB_UNLOCK_PROTECTED=1`. The hook then allows everything, so only do this yourself, never as an instruction to an agent.

### Manual verification (do after activating)

In a live session, ask the agent to edit a file inside a protected directory. Expected: the edit is refused with `BLOCKED: ... See docs/PROTECTIONS.md`. Then try a Bash write such as `echo x >> contracts/intent.py`. Expected: refused. If either goes through, the lock is not working; do not rely on it.

## What these controls do not stop

State these plainly; they are real.

- **You, at your own terminal.** The lock constrains agents using Claude Code tools. Anyone with shell access as the same user can edit, chmod, or unset the variable.
- **Bash that avoids the pattern.** A formatter that rewrites files (`ruff format`, `make fmt`), a glob that expands to a protected path (`h*/a.py`), a script file run by an interpreter, an `awk`/`sed` variant not listed, or a tool installed later is not detected. The check is a substring heuristic. The real control would be denying Bash writes outright or an OS-level read-only flag.
- **MCP and other tools.** The hook matches Claude Code's file tools and Bash only. A filesystem MCP server's write tools are not matched.
- **The hook failing to start.** If `python3` is missing the hook exits 127, which Claude Code treats as non-blocking. The script cannot guard against that.
- **Allowed-library I/O.** numpy, pandas and scipy are off the strategy allow-list because they can read arbitrary files; they return when the Phase 4 sandbox exists.
- **Untrusted strategy code.** The import-linter contracts cover only this repo's packages, so a strategy can still `import os`, `socket` or `sqlite3` and read the registry, files or the holdout. `tools/check_strategy_sandbox.py` is a static tripwire for that, and `DataView._provider` can be reached by code that holds the view. Neither is a sandbox. Process-level isolation (no filesystem or network for strategy code) is a Phase 4 requirement before the proposer runs.
- **Shallow immutability.** `Intent.weights`, `Trial.params`/`metrics` and `Command.payload` are read-only mappings, but nested JSON values inside `params` and `payload` can still be mutated.
- **Read access.** Agents can read the harness. That is intended (the proposer may read it), but it also means the harness source is visible to anything the proposer can influence. The hidden holdout must therefore live outside the repo and never be exposed in detail.
- **Hooks reaching teammates.** Whether Agent Teams teammates and subagents run the same hooks is **unverified**. Test this at the start of Phase 1 before any teammate writes code. If they do not, use git worktrees plus per-teammate path rules in prompts, and rely on CODEOWNERS review.
- **Per-teammate directory ownership.** The hook protects paths for everyone equally; it does not yet restrict teammate A to `harness/data/` only. Ownership is enforced by prompts, worktrees, and review until a per-agent mechanism is confirmed.
- **CI is not verified.** The workflow cannot run until you push. `make check` runs the same commands locally. Action versions in the workflow are pinned to major tags that I could not verify as current; pin to commit SHAs when you first push.
- **Postgres.** The Postgres DDL has been pattern-checked only: it has never been parsed or run against a real database, and `uuid-ossp` must be available on the host (check on Neon).
- **CODEOWNERS handle.** `@Nishi46` was inferred from the git remote URL and is unverified. A wrong handle silently disables review requirements; confirm it before relying on CODEOWNERS.

## Changing a contract

1. A teammate who needs a change messages the lead with the change and the reason. They do not edit the file.
2. The lead brings it to the owner. Only an owner-approved change is merged (CODEOWNERS enforces review).
3. The lead tells every teammate when a contract changes, and the shared contract suite is updated in the same change.

## Adding a dependency

`pyproject.toml` and `uv.lock` are protected. A teammate needing a library asks the lead, who adds it in a separate reviewed change. This keeps new packages (a supply-chain risk) from arriving unreviewed.
