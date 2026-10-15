# Contracts

The frozen interfaces between components. They live in `contracts/`, a leaf package that imports nothing else in this repo. Phase 1 teammates build against these and may not change them (see "Changing a contract").

Import from the package root: `from contracts import Intent, DataView, ...`.

## Rules that apply to every contract

- **UTC only.** Every datetime is timezone-aware and normalized to UTC. Naive datetimes are rejected.
- **Immutable.** Models are frozen, and mapping fields (`Intent.weights`, `Trial.params`/`metrics`, `Command.payload`) are copied into read-only views at validation. Immutability is shallow: nested JSON inside `params` and `payload` is still mutable. Registry and event logs are append-only; nothing is updated or deleted.
- **Capital is explicit.** `CapitalConfig` is passed in, never read from a global. Default is 10,000 USD. Results at one capital level say nothing about another.
- **Survivorship is disclosed.** `Snapshot.survivorship_biased` and `Trial.survivorship_biased` have no safe default on `Trial`; free data is always biased.

## The contracts

| Contract | What it is | Key invariants |
|---|---|---|
| `CapitalConfig` | Account size for a run | Amount positive and finite; `source` is `default`, `env` or `website` |
| `Intent` | What a strategy wants | Long-only target weights in [0, 1], total at most 1, empty means cash. No shares, dollars or order fields |
| `Strategy` | Protocol: `generate(data, asof) -> Intent` | `asof` must equal `data.asof`; engine enforces. Strategies never see a broker |
| `DataProvider` | Protocol: snapshot, bars, corporate actions, index membership | Never returns data after `end`/`on`. `snapshot().id` is a content hash |
| `DataView` | The one concrete class: a provider limited to `asof` | Raises `LookaheadError` on any read past `asof`, and also when a provider leaks future data. Do not reimplement. It guards against honest mistakes and buggy providers; code that holds the view can still reach `_provider`, so untrusted strategy code needs a real sandbox (Phase 4) |
| `CostModel` | Protocol: cost in USD for an order | Depends on order size and market liquidity; larger orders never cost proportionally less |
| `Splitter` / `Split` | Purged walk-forward splits | `train_end < test_start`; `purge` and `embargo` counted in bars; no random k-fold |
| `Registry` / `Trial` | Append-only trial log | No update/delete methods exist. `count()` feeds the Deflated Sharpe trial count. Duplicate id raises `DuplicateTrialError` |
| `RunLog` / `RunEvent` | Append-only engine heartbeat | `latest()` is the last appended event; the console flags a stale one |
| `CommandInbox` / `Command` | Website-to-engine commands | `submit` records `PENDING`; status only moves by appending history; unknown command raises `UnknownCommandError` |

`Stage` lists the validation pipeline in its fixed order: lookahead, walk_forward, trial_adjusted, cost_sensitivity, stability, floors, paper, owner_gate.

## Database schema

`contracts/sql/registry_sqlite.sql` (local dev, tests) and `contracts/sql/registry_postgres.sql` (production). Four tables: `trials`, `runs`, `commands`, `command_status_history`. UUID ids, TEXT not VARCHAR, TIMESTAMPTZ in UTC, no foreign keys, no soft delete (append-only by design). Triggers block UPDATE and DELETE (and TRUNCATE on Postgres).

The SQLite DDL is exercised by tests, including attempts to rewrite a row with `INSERT OR REPLACE` or an upsert. The Postgres DDL is only pattern-checked in Phase 0: it has never been parsed or run, and a live Postgres test is a Phase 1 task.

## Testing your implementation

`tests/contract_suite/` holds tests that every implementation must pass. Fakes in `fakes/` pass them today. To cover a real implementation, add its factory to the `params` list of the matching fixture in `tests/contract_suite/conftest.py`.

## Changing a contract

Contract files are CODEOWNER-protected. A teammate who needs a change does not edit the file; they message the lead with the change and the reason. The lead brings it to the owner, and only an owner-approved change is merged. All teammates are told when a contract changes.
