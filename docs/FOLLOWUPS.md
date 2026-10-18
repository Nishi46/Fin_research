# Follow-ups

Decisions the owner has made that are not built yet. Each has a trigger, so none is forgotten.

## True PBO, before the proposer agent (build step 5)

**Precondition for step 5: do not build the proposer agent until this is done.** The Deflated
Sharpe Ratio already deflates for the number of trials, but PBO asks a different question, whether
the in-sample winner of many genuinely different candidates holds up out of sample, and it matters
most once an agent is proposing many candidates.

Why it is not in Phase 1: PBO needs the daily returns of many different trials. The pipeline now
computes PBO over a candidate and its parameter neighbours, which are near-identical strategies,
and that measures which neighbour wins, not overfitting (it rejected the planted-edge positive
control at PBO 0.785). It is recorded in every trial's metrics but does not gate.

What it needs:
- A registry table for per-trial return series (append-only, same triggers as the rest), a new
  `Registry` method to write and read them, and the SQLite and Postgres DDL. This changes the
  frozen contracts and needs the owner's approval. Size: about 8 KB per 1,000 days, roughly 0.8 GB
  per 100,000 trials, so consider float32 or compression.
- A grouping key, so the trials compared share one data snapshot and window and have aligned
  timestamps.
- Pipeline wiring: CSCV over the batch plus the candidate, gated on `pbo_max`. The cost grows with
  the batch size, so measure before choosing the partition count.
- Adversary tests: best-of-N noise should show high PBO, and a genuine edge among noise should show
  low PBO.

`harness/` is locked after the Phase 1 gate, so this is an owner-unlocked change.

## Calibrate the data reconciliation tolerances (done 2026-10-18, recalibrate when universe widens)

Ran `scripts.reconcile_sources` (Alpaca IEX vs yfinance, 10 large caps, 3 years, 7500 bars):
p50 0.016%, p99 0.15%, p99.9 0.88%, max 2.3%; 4 bars exceeded 1%. Owner approved:
`reconcile_price_tolerance` stays 1%, `reconcile_max_disagree_fraction` 2% -> 0.5%,
`reconcile_min_overlap` stays 90%. Caveats: mega-caps only, one run, and the worst bars cluster on
a few high-volume dates (likely IEX last-trade vs consolidated close). Smaller ETFs and less
liquid names will differ more. This meets the precondition for running the holdout carve on real
data (a second source reconciles).

## Alpaca corporate-actions path (resolved)

Smoke test run with paper keys: `/v1/corporate-actions` is correct and stays the default
(`/v1beta1` also returns 200). The earlier HTTP 400s were caused by `limit=10000`; this endpoint
caps `limit` at 1000 (bars allow 10000). Fixed with `ACTIONS_PAGE_LIMIT` in
`harness/data/alpaca.py`.

## Holdout

Decided by the owner on 2026-10-18:
- **Length and period:** the most recent 2 years of data (`holdout_years = 2`, about 504 daily
  returns; t >= 2 then needs an annualized Sharpe of about 1.4). Research and walk-forward use
  everything before it.
- **Lifetime cap:** 20 queries in total across all strategies (`holdout_max_queries = 20`). Built:
  `HoldoutGate` refuses with `HoldoutQueryCapError` once the ledger holds 20 verdicts. Replays and
  runner errors do not spend a query. Raising the cap is an owner change to the thresholds.

Spec: `.planning/specs/holdout-carve-out.md` (owner decisions recorded there). Preconditions it adds:
- **Before the carve is run on real data:** a second source (Stooq or yfinance) wired in and
  reconciling with Alpaca on the full snapshot.
- **Before build step 5 (proposer agent):** the proposer runs under a separate OS user or
  container that cannot read the holdout directory. Also add `permissions.deny` Read rules for
  the holdout directory in the Claude Code settings (owner action, once the path is chosen).

Still open: nothing carves the holdout out of the data yet. That needs a data-split step that
reserves the last `holdout_years` of the pinned snapshot outside the repository (the holdout
directory), with an embargo gap between the research window and the holdout for multi-bar labels,
and the research side must be unable to read it. Spec this before the Phase 1 lock.
