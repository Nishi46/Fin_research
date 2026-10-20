# Spec: True PBO (Probability of Backtest Overfitting across trials)

**Created**: 2026-10-19
**Status**: design approved by the owner on 2026-10-19 (including the contract change and the unlock). Still open: the plan, the R7 runtime measurement before the three threshold values are frozen, and open question 3 (dedupe by code hash). Blocked on a session launched with `LAB_UNLOCK_PROTECTED=1` for the protected edits.
**Author**: Nishi Kantamneni
**Epic**: none (precondition for build step 5, the proposer agent; see docs/FOLLOWUPS.md)

## Problem

`harness/validation/pbo.py` implements CSCV correctly, but the pipeline feeds it a candidate and its parameter neighbours. Those are near-identical strategies, so the statistic measures which neighbour wins, not whether the in-sample winner of many genuinely different candidates holds up out of sample. It rejected the planted-edge positive control at PBO 0.785 and therefore does not gate (`pbo_gating = 0.0`). Deflated Sharpe already deflates for the number of trials, but PBO asks a different question, and it matters most once an agent is proposing many candidates.

## Goal

A PBO that is computed over the daily returns of many genuinely different trials, gates the trial-adjusted stage once enough peers exist, and never needs a trial's returns to be edited or deleted. Success is checkable with the adversary suite: best-of-N noise shows high PBO, and a genuine edge among noise shows low PBO.

## User Stories

- As the **owner**, I want a candidate's PBO to be judged against every earlier trial on the same data and window, so that "best of N" selection is penalised by the real N and the real field.
- As the **future proposer agent**, I want a single pass/fail at the trial-adjusted stage, not the PBO number, so that I cannot tune to it.
- As a **reviewer**, I want each trial's return series kept append-only next to its registry row, so that any PBO can be recomputed later.

## Requirements

### Must have

**R1. Per-trial return series in the registry (contract change, owner approval needed).** `Registry` gains `append_returns(trial_id, group_key, timestamps, returns)` and `returns_for_group(group_key, limit, before)`; there is still no update or delete. A series can be appended once per trial. Storage: a new table `trial_returns(trial_id PRIMARY KEY, group_key, n, payload BLOB)`, payload = int32 day numbers plus float32 returns, zlib-compressed. About 8 KB per 1,000 days before compression.

**R2. Append-only enforcement for the new table.** SQLite triggers that abort UPDATE and DELETE, matching the existing `trials` table; Postgres DDL with triggers and an engine role without UPDATE/DELETE grants.

**R3. Group key.** Trials are compared only if they share a `group_key` = hash of (data snapshot id, window start, window end, cost multiplier, capital amount and currency, frequency "daily"). Series in one group must have identical timestamps; the writer refuses a series whose timestamps differ from the group's first series.

**R4. Pipeline wiring.** After walk-forward produces the candidate's net daily returns, store them under the group key, then compute CSCV over the candidate plus up to `pbo_max_peers` earlier trials in the same group (most recent first). Every trial that gets a return series counts, including ones later rejected: dropping the failures would hide the real N.

**R5. Gate.** At the trial-adjusted stage, fail when PBO > `pbo_max` once the group has at least `pbo_min_trials` trials. Below that, record `pbo_gating = 0.0` and the reason (insufficient peers), as today. The existing neighbour-based number stays as an informational metric `pbo_neighbours`.

**R6. New thresholds** in `harness/thresholds.py` (owner approves values): `pbo_min_trials`, `pbo_max_peers`, `pbo_n_blocks`. Proposed: 20, 200, 16.

**R7. Cost measured before it is chosen.** CSCV cost grows with the number of trials and with C(blocks, blocks/2) (12,870 combinations at 16 blocks). Measure runtime at 20, 100 and 200 trials before freezing `pbo_max_peers` and `pbo_n_blocks`.

### Nice to have

- A `verify` command that recomputes PBO for a group from stored series, for audits.

### Out of scope

- Changing `pbo_max` (0.5), DSR, or any other stage.
- Postgres in production beyond the DDL and the same trigger semantics; the Postgres registry itself is a later step.
- Using PBO as a score for the proposer: it only produces pass/fail.

## Data Model

```
trial_returns
  trial_id    TEXT PRIMARY KEY      -- same id as trials.id (no foreign key, per project rules)
  group_key   TEXT NOT NULL
  n           INTEGER NOT NULL
  payload     BLOB NOT NULL         -- zlib(int32 day numbers + float32 returns)
  created_at  TIMESTAMPTZ NOT NULL
INDEX idx_trial_returns_group ON trial_returns(group_key, created_at)
TRIGGERS: abort UPDATE, abort DELETE
```

float32 loses precision beyond about 7 digits; acceptable for daily returns, and a test must show the PBO computed from stored series matches the one from the original float64 within tolerance.

## API Changes

Protocol additions in `contracts/registry.py`:

```python
class ReturnSeries(BaseModel):  # frozen
    trial_id: uuid.UUID
    group_key: str
    timestamps: tuple[UtcDatetime, ...]
    returns: tuple[float, ...]


def append_returns(self, series: ReturnSeries) -> None: ...
def returns_for_group(
    self, group_key: str, *, limit: int, exclude: uuid.UUID | None = None
) -> Sequence[ReturnSeries]: ...
```

`DuplicateTrialError` is reused for a second series for the same trial.

## UI Changes

None.

## Edge Cases

1. **Fewer than `pbo_min_trials` peers:** no gate, reason recorded; never a silent pass of the gate's wording.
2. **Group timestamps differ** (calendar change, partial data): refuse the append with a clear error; the trial still logs, with PBO "not computed".
3. **Constant (zero-variance) series:** CSCV already gives Sharpe 0; test it.
4. **Identical series from re-submitted code:** a re-run is a new trial (new id), so it enters the group again; PBO treats duplicates as extra peers. Document it, or dedupe by code hash.
5. **Candidate with fewer rows than blocks:** not computed, recorded as such.
6. **Return storage failure after the trial row is written:** the trial row stays (append-only); the trial has no series and is excluded from later PBOs; surfaced as a metric.
7. **Race:** two trials appended at once to one group; ordering by `created_at` only affects which peers are the "most recent", not correctness.

## Testing Criteria

- Contract suite: new Registry methods on every implementation (fake and SQLite); append-once; no update or delete (triggers abort); group isolation; ordering and limit.
- Storage: float32 round trip within tolerance; compression is lossless for timestamps; payload size at 1,000 and 2,500 days.
- Pipeline: gates when peers >= `pbo_min_trials`; informational below; candidate excluded from its own peer set.
- Adversary: best-of-N noise rejected by high PBO; a genuine planted edge among noise passes (low PBO); results stable across seeds.
- Performance: a measured table of runtime vs N and blocks (R7).

## Dependencies

- **Owner approval and an unlock.** R1 changes `contracts/registry.py`, `tests/contract_suite/` and `fakes/` (protected paths). The owner launches Claude Code with `LAB_UNLOCK_PROTECTED=1` for the contract-change commit, or makes that change themselves.
- `harness/registry/` (SQLite, DDL) and `harness/validation/{pbo,pipeline}.py` are unprotected until `harness/` is locked. Do this work before the Phase 1 lock, or unlock again after.
- Thresholds approved by the owner (R6).

## Open questions for the owner

1. Approve the contract change (R1) and the unlock to make it.
2. Threshold values: `pbo_min_trials = 20`, `pbo_max_peers = 200`, `pbo_n_blocks = 16`, pending the R7 measurement.
3. Dedupe re-submitted identical code by `code_hash` (edge 4), or let duplicates count as extra peers?
