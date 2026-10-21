# Phase 1 harness review (owner gate)

Prepared 2026-10-19 for the owner. This is a reading guide, not an approval. The lock is the owner's
decision after reviewing the diff; nothing here has been reviewed by anyone but its authors.

Baseline: commit `db7c06e` (the original Phase 1 harness). Everything below changed in `harness/`
after it. Run `git diff db7c06e -- harness` for the full diff (about 18 files, 1,050 lines added,
counting the PBO work).

**Row 0, read first: cross-trial PBO (new, 2026-10-20).** It also changes the protected contract
surface, so review these together: `contracts/registry.py` (`ReturnSeries`, `append_returns`,
`returns_for_group`), `contracts/sql/registry_{sqlite,postgres}.sql` (the SQLite file was edited to
`IF NOT EXISTS` throughout; existing tables unchanged), `fakes/registry.py`,
`harness/registry/{_db,sqlite}.py`, `harness/validation/{peers,dsr,pipeline}.py`,
`harness/thresholds.py` (`pbo_min_trials=30`, `pbo_max_peers=200`, `pbo_n_blocks=16`). Things to check:
the return series is append-only (triggers abort update, delete and replace); a candidate is excluded
from its own peer set; the gate only applies once the group has 30 trials. Known property, not a bug:
the 0.5 gate is noisy (it rejects 40 to 50 percent of pure-noise candidates and 8 to 15 percent of
genuine Sharpe-1.0 edges; Sharpe 2.0 is essentially never rejected), and re-submitted identical code
counts as an extra peer by owner decision.

## Verification state (updated 2026-10-20)

- `uv run pytest`: 1131 passed, 1 skipped (a membership leak test that cannot be written, see below)
  and 1 failure that appears only in a session launched with `LAB_UNLOCK_PROTECTED=1`:
  `test_config_ignores_the_process_environment` rejects any `LAB_*` variable. It passes (6/6 in that
  file) with the variable unset. Run the suite from a normally launched session for the real result.
- `ruff check`, `ruff format --check`, `mypy harness scripts`, `lint-imports` (5 contracts) all clean.
- The holdout was carved on real data on 2026-10-19 and verified (`verify_holdout_dir`).
- True PBO was built on 2026-10-20 (see row 0 below). Until it is committed, `git diff db7c06e` also
  includes it.

## What changed, in the order I would read it

| Priority | File | Change | Why it matters for trust |
|---|---|---|---|
| 1 | `harness/validation/holdout.py` | The gate requires a verified manifest, checks directory mode and content hash, scores only returns in `[holdout_start, holdout_end]`, enforces a 20-query lifetime cap | This is the hidden-holdout invariant. Check that nothing but a boolean leaves `evaluate`. |
| 2 | `harness/data/holdout_store.py` (new) | Atomic one-time write, manifest, hash over files plus carve parameters, write-once, directory mode 0700 | Tamper and re-carve protection. |
| 3 | `harness/data/window.py` (new) | `compute_windows`; `ResearchWindow` clamps every read at `research_end` | The research side must not see holdout data. Note the membership limit below. |
| 4 | `harness/data/split.py` (new) | Partition by session date into research, embargo, holdout; refuses short windows and data after `holdout_end` | Accepts unsorted input; only duplicates are refused. |
| 5 | `harness/thresholds.py` | Added `holdout_years`, `holdout_max_queries`, `holdout_embargo_sessions`, `holdout_min_session_fraction`, `holdout_min_research_years`; `reconcile_max_disagree_fraction` 2% to 0.5% | Frozen rules. Every value was owner-approved on 2026-10-18. |
| 6 | `harness/data/provider.py` | Cross-source reconciliation now on a split-only basis; strict snapshot also rejects spin-offs, naming the symbols | The first fix was a real bug found by the real carve (dividend-adjusted prices vs Yahoo's split-only close). |
| 7 | `harness/data/alpaca.py`, `config.py` | Default feed is SIP; `end` capped at the start of the current UTC day; `fetch_spin_offs`; corporate-actions page limit 1000 | Changes the data basis for everything. |
| 8 | `harness/data/{cache,common,quality,reconcile,crosscheck,yfinance_fetcher}.py` | `ActionsStore`, `SpinOff` type and `find_spin_offs`, yfinance fetcher, small reconcile and crosscheck tweaks | Supporting changes. |

## Things to ask when reviewing

1. Can anything on the research side return a bar dated after `research_end`? (Tests: parametrized
   leak test over every `DataProvider` method, plus the end-to-end test. One gap: see below.)
2. Can a caller widen the scored holdout window or move the boundary? (It comes from the manifest,
   which is hash-checked.)
3. Is the 20-query cap spendable only by distinct code versions, and does a runner error or a replay
   leave it unspent? (Tested.)
4. Do the thresholds file and the registry still contain only rules and rows the owner approved?

## Known gaps (not closed by code)

- **Membership.** `ResearchWindow` can only clamp the date for index membership; a provider built from
  uncapped membership data would leak. The test for it is skipped on purpose.
- **Gate runner.** `HoldoutGate.evaluate(run=...)` hands the holdout path to a caller-supplied callable.
  The harness must build that callable, never the proposer. Software cannot close this; see
  `docs/PROPOSER_ISOLATION.md`.
- **Provenance.** The carve manifest records `code_version` `8d9007c`, but 12 files were uncommitted
  when it ran. See `docs/FOLLOWUPS.md`.
- **Spin-offs** are detected and refused, not adjusted. ETF carve-outs without a spin-off record (XLF in
  2016) are caught only by the Yahoo reconciliation.
- **Data quirk.** A few SIP sessions carry a tiny-volume bar for many symbols and no check flags it.

## Independent review results (2026-10-20)

Four read-only reviewers (holdout gate and store, cross-trial PBO, data layer and carve script,
invariants and thresholds) examined the diff. They found no critical defect and nothing that touches
the carved holdout data. Every finding below is either fixed with a test that failed first (or was
shown to bite by mutation), waiting for an owner decision, or deliberately deferred.

### Fixed (test first)

| Area | Finding | Fix |
|---|---|---|
| Pipeline | The trial append was not in a `finally` despite the docstring, so a failure while building a trial could skip logging it and leave an orphan return series that still counted as a PBO peer | Trial is built and appended in a `finally` with a minimal ERROR-trial fallback; KeyboardInterrupt and SystemExit are logged then re-raised; a failed append logs CRITICAL |
| Gate | `min_t_stat` and `max_queries` could be set by the caller (0.0 passes constant-zero returns; a huge cap) | Constructor requires `min_t_stat` finite and at least 2.0, `max_queries` an int in [1, 20] |
| Gate | `"passed": "false"` in the ledger replayed as True | Strict boolean; anything else raises a ledger error |
| Gate | Ledger: torn line fused two entries, blank lines counted as queries, no locking | Newline repair, blank lines ignored, malformed lines raise naming the line, `flock` around lookup, run and append, fsync |
| Gate | Manifest not checked for sanity; symlinks followed; no re-check after the run | Date-coherence and contents-versus-manifest checks, symlinks rejected, re-verify after `run` |
| Gate | NY versus UTC session filter untested | Boundary tests at 01:00, 03:00, 04:00, 18:00 and 21:00 UTC; the mutation to `ts.date()` is caught |
| PBO | A refused return series silently switched the PBO gate off | Always records `pbo_refused`; fails the stage once the group is established |
| PBO | Group key sensitive to UTC offset and Decimal formatting; candidate float64 vs peers float32 | Keys normalised; candidate round-tripped through float32 |
| PBO | Every sqlite IntegrityError became DuplicateTrialError; `pbo_min_trials` allowed 2 | Only duplicate cases map; lower bound now 10 (default stays 30) |
| Reconcile | One symbol with zero Yahoo coverage could pass on pooled overlap | Fails when any symbol has no overlap at all (no threshold changed) |
| Alpaca | Paging could loop forever; reverse-split direction untested | Guard on repeated token and page cap; direction pinned by tests (code was already right) |

### Needs an owner decision

**Decided 2026-10-20:** (1) PBO is documented and worded as a field-level diagnostic; (2) the code
keeps storing series only for walk-forward survivors and spec R4 is amended (R4a, R4b in
`.planning/specs/true-pbo.md`); (3) the PBO group key now includes a cost-model fingerprint
(`cost_model_fingerprint` in `harness/validation/peers.py`; `None` equals the explicit default model;
an unreadable model fails closed); (4) `run_pipeline` refuses a provider that is not a
`ResearchWindow` (TypeError) and an `end` beyond it (ValueError), before any trial is logged;
(5) the strategy interface is defined: `ParametrizedStrategy` in `contracts/strategy.py` (`params`,
`with_params`, `lookback_bars`, stateless and deterministic), `BaselineReversal` conforms, a
conformance suite lives in `tests/contract_suite/test_strategy_contract.py`, and `run_pipeline` can be
called without `build`/`params` (spec `.planning/specs/strategy-interface.md`). Only item 6 (where
strategy code runs) is still open, and it is build step 5 design. The original text of 1 to 6 follows.

1. **PBO is field-level, not per-candidate (HIGH).** CSCV asks whether the best of the whole field holds
   up out of sample. If the group contains one genuinely strong peer, a junk candidate also sees a
   near-zero PBO and passes that gate (reviewer's experiment: a losing candidate scored 0.0003 beside one
   strong peer). On pure noise the gate is a coin flip. Options: (a) gate on a candidate-specific
   quantity, such as the candidate's own in-sample-to-out-of-sample rank logits; (b) keep it and state
   in the spec that PBO is a field-level overfitting diagnostic, with DSR and the other stages doing the
   per-candidate rejection. This does not make the lab less safe (the gate only adds rejections) but
   "PBO rejected this candidate" must not be read as "this candidate overfits".
2. **Which trials contribute a series.** The spec said every trial, including rejected ones. The build
   stores a series only for trials that pass lookahead and walk-forward. Keeping that is arguably better
   (a lookahead cheater's huge returns would dominate the field and push PBO toward zero), but it means
   about 100 submissions are needed to reach 30 peers, and the real N is only in DSR. Recommend: keep the
   code, amend spec R4.
3. **Group key and cost model.** The key includes `cost_multiplier` but not the cost model itself, so
   two trials run with different `CostModel` objects share a group. Add a cost-model fingerprint?
4. **Research-window assertion.** `run_pipeline` trusts the caller to pass a provider already clamped to
   the research window; `check_lookahead` asks for `FAR_FUTURE`. Require a verified `ResearchWindow`?
5. **Strategy interface stub (build step 2) is incomplete.** The pipeline needs a `build(params)`
   callable and a params mapping for neighbour and stability checks, but `contracts/strategy.py` defines
   neither, and `BaselineReversal` has no `build`. Also undefined: warm-up lookback, declared universe,
   statefulness (a stateful strategy makes the lookahead comparison unreliable), exceptions, timeouts.
6. **Where strategy code runs.** See `docs/PROPOSER_ISOLATION.md`: grading executes strategy code
   inside the process that holds the holdout path. The sandboxed run loop is the main open item for
   build step 5.

### Deferred, with reasons

- **Walk-forward is a stability slice** (purge 0, nothing fitted per fold, first block unscored). Known
  and documented in the stage; real purge and embargo matter once something is fitted.
- **Lookahead check times** are a deterministic grid of 20; a leak confined to a sparse set of dates could
  slip between them. Candidate fix: randomised, seed-recorded times.
- **Literals outside `Thresholds`:** `BacktestConfig` defaults (`adv_window` 20, `rebalance_band` 0.02),
  the adjustment jump constants, `MIN_STALE_RUN`, `MIN_POINTS`, the lookahead warm-up fraction. Not
  owner-recorded. Worth moving into the frozen object before the lock if you want them frozen formally.
- **Tiny-volume SIP bars** are not flagged by any quality check (needs a threshold you set, such as 1% of
  the trailing median).
- **Carve-script findings** (missing-symbol check, atomicity, error output): moot, because a second carve
  is refused by design. The data it wrote was verified separately.
- **Guards not wired outside the carve:** `holdout_provider` and the `composed` factory are non-strict
  and take no spin-offs; spin-offs are not persisted. Matters when the universe changes.
- **Cache `fetch_incremental`** is unused; its key ignores the feed.
- **`holdout_min_t_stat = 2.0`** is in the thresholds file and the original FOLLOWUPS but had no entry in
  the later specs; it is recorded again in FOLLOWUPS now.
- No per-strategy timeout exists; a hanging strategy blocks the pipeline.

## What is next (as of 2026-10-20)

True PBO has landed, so the lock now covers everything and the diff to review is final. Order:

1. **Commit** the PBO work from the unlocked session (it touches `contracts/`), then quit and relaunch
   VS Code normally so the protections are back.
2. **Review** the harness diff using the table above, starting with row 0 and `holdout.py`, and decide
   whether anything needs changing first.
3. **Lock** `harness/` (next section). After this, harness edits need an unlocked launch and a fresh
   review. Doing the lock before the proposer exists is the point.
4. **Proposer isolation** (`docs/PROPOSER_ISOLATION.md`): choose container or separate user and run its
   verification checklist. Required before build step 5.
5. **Build step 2**, the strategy interface stub (intents only), then the risk layer (step 3).

The memory note also asks to confirm PBO is committed before build step 5.

## How to lock (owner action; the protected-paths file is itself protected)

Add one line to `.claude/protected_paths.txt`, after reviewing:

```
harness/
```

Also consider adding `scripts/carve_holdout.py` (the carve is done and must not be altered) and
`docs/PHASE1_HARNESS_REVIEW.md` is not needed in the list. After the lock, any harness change needs
`LAB_UNLOCK_PROTECTED=1` at launch, and should go through this same review.

Check the lock took effect by asking Claude Code to edit a harness file: the hook should refuse.
