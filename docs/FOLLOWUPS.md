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

## Calibrate the data reconciliation tolerances

`reconcile_price_tolerance`, `reconcile_max_disagree_fraction` and `reconcile_min_overlap` are in
the frozen config as starting points. Once two real sources are wired, measure the distribution of
real differences between them and set the tolerances from it, then re-freeze.

## Alpaca corporate-actions path

The default `/v1/corporate-actions` may be wrong; Alpaca's own examples use
`/v1beta1/corporate-actions`. Run `uv run python -m scripts.alpaca_smoke_test` with paper keys
(read-only), set `ALPACA_CORPORATE_ACTIONS_PATH` to the path it reports, and make that the
default in `harness/data/config.py` before locking `harness/`.

## Holdout

The gate requires a t-statistic of at least 2. Still open: how long the holdout is and which
period it covers (nothing carves it out of the data yet), and a lifetime cap on holdout queries.
