# Spec: Holdout Carve-Out

**Created**: 2026-10-18
**Status**: draft
**Author**: Nishi Kantamneni
**Epic**: none (finishes the "Hidden final holdout" invariant from build step 1; must land before the Phase 1 lock and before the proposer agent, step 5)

## Problem

`HoldoutGate` ([harness/validation/holdout.py](../../harness/validation/holdout.py)) grades a strategy against data in `FIN_RESEARCH_HOLDOUT_DIR`, with a t >= 2 bar and a 20-query lifetime cap. But nothing puts data there. The research side (walk-forward, DSR, stability, the future proposer) reads one ordinary Parquet cache that contains every bar, so today the "hidden" holdout is not hidden: there is no holdout, only a gate waiting for one. Until the data is physically split, a result that "passes the holdout" proves nothing, and the 20-query cap bounds a leak that already happened.

## Goal

A one-time, owner-run carve that splits the pinned data snapshot into a research side and a holdout side, such that:

- the holdout is the most recent `holdout_years` (2) of data, frozen at a fixed boundary date and never moved;
- an embargo of 10 trading sessions separates the last research bar from the first scored holdout bar;
- no code path used by research, the pipeline or the proposer can return a bar dated after `research_end`, and no holdout bar is ever written inside the repository;
- `HoldoutGate` scores only returns from `holdout_start` onward, using window dates from a manifest the carve wrote, not from the caller;
- the whole thing is covered by tests that fail if a post-`research_end` bar can reach the research side.

Success is checkable: after the carve, a test that asks every research-side data entry point for data past `research_end` gets an error or nothing, and the gate scores a known-edge fixture through the real holdout directory.

## User Stories

- As the **owner**, I want to run one command that freezes the holdout and prints exactly which dates are research, embargo and holdout, so that I can confirm the split before the harness is locked.
- As the **harness**, I want the holdout window defined by a manifest written at carve time, so that no caller can widen the scored region or move the boundary to include more favourable days.
- As the **future proposer agent**, I want a research data provider that simply ends at `research_end`, so that I cannot see holdout bars even by asking for a later date range.
- As a **reviewer of any result**, I want each registry entry to record the research snapshot id and the window dates, so that I can tell which data a number was computed on.

## Requirements

### Must have

**R1. Frozen windows.** `holdout_years` (already in thresholds) and a new `holdout_embargo_sessions = 10` (`ge=1`, owner-changeable only before the carve) define the split. With `last` = the last complete session in the source snapshot:

- `holdout_start` = the first session on or after `last` minus `holdout_years` calendar years;
- `holdout_end` = `last`;
- `research_end` = the session `holdout_embargo_sessions` sessions before `holdout_start`;
- embargo sessions are those strictly between `research_end` and `holdout_start`. They belong to neither scored set.

Sessions come from `harness.data.calendar`, not weekdays.

**R2. One-time carve, refuses to repeat.** `scripts/carve_holdout.py`, run by the owner from their terminal (never by an agent), reads the pinned snapshot, writes the holdout side, then writes the research side. If a manifest already exists in the holdout directory it exits non-zero and changes nothing. A "re-carve" is not an option: moving the boundary releases ex-holdout data to research and voids the query cap.

**R3. Holdout side.** Written under `FIN_RESEARCH_HOLDOUT_DIR` (reuses `resolve_holdout_dir`: set, exists, outside the repo): bars and corporate actions for `[research_end + 1 session, holdout_end]` (embargo plus holdout), plus `holdout_manifest.json`. *(Amended 2026-10-18: ten embargo sessions cannot warm up a 200-day indicator, so a holdout run builds its provider from the research data, which is not secret, plus this directory, adjusted once at `holdout_end` (`holdout_provider`). The directory itself holds only embargo and holdout sessions.)* The directory must not be group- or world-readable (mode 0700); the carve sets it, and `HoldoutGate` refuses a looser mode.

**R4. Manifest.** `holdout_manifest.json`, written once, contains: source snapshot id, `research_end`, `holdout_start`, `holdout_end`, `holdout_years`, `embargo_sessions`, a content hash of the holdout files, carve time (UTC), and the code version. The window dates are not secret (the holdout is "the last two years"); the bar contents are.

**R5. Research side is capped.** A `ResearchWindow`-bounded `DataProvider` wrapper clamps every request to `research_end`: a request starting after it returns nothing; a request spanning it is truncated; and the same cap applies to cache loads, so a stale full cache cannot leak through. Every provider handed to research, the pipeline or the proposer is built through it. The research snapshot id is computed over the capped data and recorded in each trial. The carve itself fetches the full range once, splits it in memory, and never writes holdout-side bars into the research cache.

**R6. Gate scores only the holdout window.** `HoldoutGate.evaluate` reads the manifest, and the t-statistic uses only the returns whose timestamp is >= `holdout_start`. Embargo-period returns are dropped. Warm-up bars before `holdout_start` may be used by the strategy but never scored. A missing or altered manifest (content hash mismatch) makes the gate refuse.

**R7. Adjustment does not cross the boundary.** Both sides store raw (unadjusted) bars plus corporate actions and adjust on read, per window. Research reads adjust using only actions with ex-date <= `research_end`. A holdout run adjusts warm-up, embargo and holdout bars together using actions up to `holdout_end`, so they share one basis. *(Amended 2026-10-18: checked. `ComposedDataProvider` already adjusts raw bars with actions point-in-time at read time and `ParquetCache` already stores raw bars, so no refactor was needed. The only gap was persisting actions, added as `ActionsStore`.)*

**R8. Fail closed.** The carve refuses, with a clear message, if: the source snapshot fails the existing quality and reconciliation checks; the research side would be shorter than the pipeline's minimum history (walk-forward plus floors); the holdout side has fewer returns than `holdout_years * 252 * 0.9`; or any symbol's bars are not strictly ordered or deduplicated.

**R9. Registry visibility.** Each trial records the research snapshot id. Detailed holdout metrics still never leave `evaluate` (unchanged). *(Amended 2026-10-18: no new registry fields, since that would change a frozen contract. A research provider built from capped data has `snapshot.end == research_end`, so the research window is recorded through the existing `data_snapshot_id`; the holdout dates live in the manifest.)*

### Nice to have

- `scripts/carve_holdout.py --dry-run` prints the three windows and bar counts per side without writing anything.
- A `verify` command that re-hashes the holdout files against the manifest, for the owner to run before each reliance on the gate.

### Out of scope

- Moving or re-carving the holdout (a deliberate non-feature, see R2).
- Forward-test data. Bars after `holdout_end` come only from the pre-registered forward-test path and are never put in the research cache either.
- OS-level isolation of the proposer from the holdout directory (see "Known limit" and open question 1).
- Raising the query cap, or the t-statistic bar (owner thresholds, already decided).
- Per-strategy or per-universe holdouts. One holdout for the whole snapshot.

## Data Model

No database tables; on-disk layout and one manifest.

```
$FIN_RESEARCH_HOLDOUT_DIR/            # outside the repo, mode 0700
├── holdout_manifest.json             # written once by the carve
├── holdout_ledger.jsonl              # existing: verdict per (strategy_id, code_hash)
├── bars/{SYMBOL}.parquet             # embargo + holdout sessions, same schema as the research cache
└── actions.parquet                   # corporate actions with ex-date in that range
```

`holdout_manifest.json`:

```json
{
  "source_snapshot_id": "sha256:...",
  "research_end": "2024-10-04",
  "holdout_start": "2024-10-18",
  "holdout_end": "2026-10-16",
  "holdout_years": 2,
  "embargo_sessions": 10,
  "content_sha256": "...",
  "carved_at": "2026-10-18T...Z",
  "code_version": "..."
}
```

(The dates above are illustrative; the carve computes real ones from the snapshot.) Dates are ISO 8601, timestamps UTC. The research cache keeps its existing layout (`bars/{SYMBOL}.parquet`) but never contains a bar after `research_end`.

New thresholds: `holdout_embargo_sessions: int = Field(default=10, ge=1)`.

## API Changes

No HTTP API. New Python surface in `harness/`:

- `harness.data.window.compute_windows(sessions, holdout_years, embargo_sessions) -> Windows` (pure, `research_end` / `holdout_start` / `holdout_end`).
- `harness.data.window.ResearchWindow(provider, research_end)`: a `DataProvider` wrapper (R5).
- `harness.validation.holdout.load_manifest(dir) -> HoldoutManifest` and a content-hash check; `HoldoutGate` filters returns by `manifest.holdout_start` (R6).
- `scripts/carve_holdout.py [--dry-run]`: owner-run CLI (R2).

## UI Changes

None. The console may later show the three window dates and the queries used (`n of 20`) read-only; that belongs to build step 6.

## Edge Cases

1. **Re-run of the carve.** Manifest exists: refuse, change nothing, exit non-zero.
2. **Partial carve (crash mid-write).** Write holdout files to a temp directory, verify the hash, then rename into place; write the manifest last. A directory with files but no manifest makes the gate refuse (not "pass with no window").
3. **Stale full cache on disk.** An older cache containing post-`research_end` bars must not leak: the wrapper clamps cache loads, and the carve reports and optionally prunes such files.
4. **Symbols that start or end inside a window.** An IPO inside the holdout exists only on the holdout side; a delisting before `holdout_start` exists only in research. Allowed, and noted in the manifest counts per side. Survivorship tags are unchanged (`survivorship_biased=True`).
5. **Split or dividend in the embargo or holdout.** Research prices must not be adjusted with those actions (R7). A 2-for-1 on a holdout date must still not look like a 50% crash in holdout returns.
6. **Source snapshot not current.** If `last` is more than a few sessions old the carve warns (a stale holdout end wastes the most recent data) but proceeds on `--allow-stale`.
7. **Holdout shorter than expected** (gaps, halted days): R8 length floor refuses.
8. **Gate called before the carve, or with a hand-made directory.** No manifest: refuse (new `HoldoutNotConfiguredError`-style message), never score.
9. **Holdout directory permissions too loose.** Gate refuses with the fix command in the message.
10. **Time zone.** Window dates are session dates (US/Eastern session, stored UTC per `TIMEZONE.md`); the boundary never splits a session.

## Testing Criteria

Tests first, in the existing harness style.

**Happy path**
- `compute_windows` on a synthetic calendar gives the expected `research_end`, `holdout_start`, `holdout_end`, with exactly `embargo_sessions` sessions between.
- Carve of a synthetic snapshot: research side ends at `research_end`; holdout side starts the session after it; union of research, embargo and holdout bars equals the source; no bar appears on both sides.
- `HoldoutGate` on a fixture with a planted edge in the holdout window passes; on noise fails; returns before `holdout_start` have no effect on the verdict.

**Leak tests (the point of the feature)**
- Every research-side entry point (wrapper `fetch_bars`, cache load, snapshot builder, pipeline data loader) asked for dates after `research_end` returns nothing or raises. Parametrized across entry points so a new one cannot be added without being covered.
- A known-leaky setup (stale full cache present) still cannot return post-`research_end` bars.
- Nothing under the repo root contains holdout-side bars after a carve (scan of tracked and untracked data dirs).
- Embargo-period returns planted with a huge edge do not move the verdict.

**Failure modes**
- Re-carve refuses and leaves files byte-identical.
- Crash injected before the manifest write leaves a directory the gate refuses.
- Manifest tampering (changed `holdout_start`, changed file contents) is detected by the hash and refused.
- Loose directory mode refused; inside-repo path refused (existing test kept).
- R8 refusals: failed quality snapshot, research too short, holdout too short, unordered bars.

**Adjustment**
- A synthetic split dated inside the holdout does not alter research-side adjusted prices; a split dated in the holdout does not appear as a crash in holdout returns.

## Dependencies

- `harness/data/` (provider, cache, snapshot, calendar, adjust, quality, reconcile) and the pinned snapshot with its id.
- `harness/validation/holdout.py` and `harness/thresholds.py` (add `holdout_embargo_sessions`; `holdout_years` and `holdout_max_queries` already exist).
- Owner action: carve only after a second source (Stooq or yfinance) is wired in and reconciles with Alpaca on the full snapshot (owner decision 3), and before the Phase 1 lock.
- `harness/` is currently unlocked for Phase 1; this lands before the lock. After the lock it needs `LAB_UNLOCK_PROTECTED=1`.

## Known limit

Filesystem permissions stop other users, not the proposer: the proposer agent runs as the owner's OS user, which can read the holdout directory. This spec stops it from reaching holdout data through the harness and the research providers, and Claude Code read-deny rules cover its own file tools, but neither is a sandbox. Real isolation (a separate OS user or container for the proposer) is a precondition for build step 5.

## Owner decisions (2026-10-18)

1. **Proposer isolation: both.** Add `permissions.deny` Read rules for the holdout directory to the Claude Code settings now, and run the proposer under a separate OS user or container before build step 5. The deny rule is a tripwire for Claude Code's own tools, not a sandbox; the OS-level isolation is the real fix and a precondition for step 5. The settings directory is a protected path, so the owner adds the rule once the holdout directory path is chosen.
2. **Warm-up adjustment: raw bars plus actions, adjusted per window at run time.** See R7.
3. **Carve timing: after a second source reconciles with Alpaca.** `scripts/carve_holdout.py` must not be run on real data until Stooq or yfinance is wired in and `reconcile` passes on the full snapshot. This also unblocks the tolerance calibration in FOLLOWUPS. Until then the spec, plan and code can be built and tested on synthetic snapshots.

## Gate 1 check

- Completeness: problem, measurable goal, user stories, must / nice / out-of-scope: yes.
- Data model: on-disk layout and manifest defined; no SQL tables, so no soft-delete strategy applies.
- API design: no HTTP endpoints; Python surface listed. JSON uses snake_case, ISO 8601 UTC.
- Quality: 10 edge cases, happy-path and failure tests, dependencies listed.
- Frontend: none (no UI in this feature).
