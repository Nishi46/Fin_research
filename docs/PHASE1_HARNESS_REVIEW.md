# Phase 1 harness review (owner gate)

Prepared 2026-10-19 for the owner. This is a reading guide, not an approval. The lock is the owner's
decision after reviewing the diff; nothing here has been reviewed by anyone but its authors.

Baseline: commit `db7c06e` (the original Phase 1 harness). Everything below changed in `harness/`
after it. Run `git diff db7c06e -- harness` for the full diff (14 files, about 805 lines added).

## Verification state (2026-10-19)

- `uv run pytest`: 1084 passed, 1 skipped (a membership leak test that cannot be written, see below).
- `ruff check`, `ruff format --check`, `mypy harness scripts`, `lint-imports` (5 contracts) all clean.
- The holdout was carved on real data on 2026-10-19 and verified (`verify_holdout_dir`).

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

## Decision for the owner: lock before or after true PBO?

True PBO (`.planning/specs/true-pbo.md`) must change `harness/registry/`, `harness/validation/pbo.py`
and `pipeline.py`, plus the protected `contracts/registry.py`. If `harness/` is locked first, building
PBO needs a temporary unlock. If PBO lands first, the lock covers it too and the diff to review is
larger but final. Recommendation: build and approve PBO, then review once and lock once.

## How to lock (owner action; the protected-paths file is itself protected)

Add one line to `.claude/protected_paths.txt`, after reviewing:

```
harness/
```

Also consider adding `scripts/carve_holdout.py` (the carve is done and must not be altered) and
`docs/PHASE1_HARNESS_REVIEW.md` is not needed in the list. After the lock, any harness change needs
`LAB_UNLOCK_PROTECTED=1` at launch, and should go through this same review.

Check the lock took effect by asking Claude Code to edit a harness file: the hook should refuse.
