# Follow-ups

Decisions the owner has made that are not built yet. Each has a trigger, so none is forgotten.

## True PBO, before the proposer agent (build step 5)

Spec written 2026-10-19: `.planning/specs/true-pbo.md`. BLOCKED on the owner: it changes the
Registry contract, its contract suite and the fakes (protected paths), so it needs the owner's
approval and an unlock (`LAB_UNLOCK_PROTECTED=1`). Not started beyond the spec.

Also written 2026-10-19: `docs/PROPOSER_ISOLATION.md` (design and runbook; not set up, needs the
owner's machine and sudo) and `docs/PHASE1_HARNESS_REVIEW.md` (reading guide for the lock).

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

CARVED 2026-10-19 (16:58 UTC), irreversible: 45 symbols, source snapshot
`sha256:1a72ac361a5419bf324e24a827daa931`, research 2016-01-04 to 2024-10-01 (99,045 bars), 10
embargo sessions, holdout 2024-10-16 to 2026-10-16 (22,590 bars), manifest content hash
`ea0b577d6331...`. Verified afterwards with `verify_holdout_dir` (mode 0700, hash matches); the
research cache ends exactly at `research_end`. The holdout dir is `~/lab-holdout`, the research
cache `~/lab-data/cache`.
Provenance caveat: the manifest's `code_version` is `8d9007c`, but 12 files were uncommitted when
the carve ran (SIP feed default, split-only reconciliation, the client's end cap, doc edits). Those
were committed afterwards; to reproduce the carve use that later commit, not `8d9007c`. The data
itself is pinned by the snapshot id and content hash.

Built (2026-10-18): `harness/data/{window,split,holdout_store}.py`, `ActionsStore`
in `cache.py`, the manifest-checking `HoldoutGate`, and `scripts/carve_holdout.py`. Original
pre-carve checklist, now done:
- approve the symbol list (`--symbols`; the universe is the owner's decision),
- `mkdir` the holdout directory outside the repo and set `FIN_RESEARCH_HOLDOUT_DIR` (the script
  requires it to exist) and `LAB_DATA_CACHE_DIR`,
- run `--dry-run` first and check the three windows,
- remember the carve is irreversible and refuses to repeat.
Carve universe (owner decision 2026-10-18), 45 symbols. ETFs: SPY QQQ IWM DIA MDY XLE XLK XLV XLY
XLP XLI XLU XLB TLT GLD HYG SMH. Stocks: AAPL MSFT NVDA AMZN GOOGL META TSLA AVGO JPM BAC V MA UNH
JNJ LLY PG KO PEP WMT COST HD XOM CVX CAT DIS NFLX AMD ORCL. Dropped: GE and HON (spin-offs) and
XLF (Sept 2016 real-estate carve-out into XLRE; about 23% off Yahoo for Jan-Sep 2016, which the
40% jump detector would not catch). The symbol list freezes with the holdout.

Data feed (owner decision 2026-10-18): the default Alpaca feed is now the consolidated SIP tape
(`harness/data/config.py`; `ALPACA_DATA_FEED=iex` still selects IEX). The free tier serves SIP daily
bars from 2016-01-04, with queries required to end 15+ minutes ago, so the client now caps `end` at
the start of the current UTC day (also keeps today's still-forming bar out). Vetted on SIP with
`scripts.reconcile_sources --years 11`, 46 symbols: 0 gaps, 2713 bars each (2016-01-04 to
2026-10-16), median difference vs Yahoo 0.00%, p99 0.05%; excluding XLF about 0.02% of bars exceed
1% (gate 0.5%). On IEX the same symbols had 1046 gaps, history only from 2020-07 and 3.2% bad bars.
Research will span about 8.8 years (2016 to late 2024), including 2018, the 2020 crash and 2022.
Known SIP quirk: a few sessions (2021-04-19, 2021-10-25, 2022-03-08) carry a bar with a tiny volume
(JPM 160k vs ~13M) for many symbols; prices look plausible, the quality checks do not flag it, and
liquidity caps would treat those days as illiquid.

Spin-offs and ETF carve-outs are not handled (found by the vetting runs). GE (GEHC 2023-01, GEV
2024-04) and HON (SOLS 2025-10, HONA 2026-06 plus a 1-for-2 reverse split) disagreed with Yahoo on
924 and 1486 bars, and XLF on 182, because Yahoo adjusts history for them and `adjust.py` handles
only splits and dividends. The Alpaca parser does not request `spin_off` events, so such a day looks
like a 20-50% crash in a backtest. GUARD BUILT 2026-10-19: `AlpacaDataClient.fetch_spin_offs`,
`find_spin_offs`, and the strict snapshot (and so the carve) now refuse any symbol with a spin-off
inside its data window, naming it. A scan of the 45 carved symbols (2016 to 2026) found none, so
the frozen universe is clean. Still open: actually adjusting for spin-offs (needs the spun-off
entity's price), and ETF carve-outs that have no spin-off record (XLF 2016), which only the Yahoo
reconciliation catches.

Known gaps: `ResearchWindow` can only clamp the date for index membership, so the research
provider must be built from membership data that is already capped; and `HoldoutGate.evaluate`
takes a caller-supplied runner that receives the holdout path, so the runner must be built by the
harness, never the proposer (the OS-level isolation above is what really closes this).

Original gap (now built): nothing carved the holdout out of the data. That needs a data-split step that
reserves the last `holdout_years` of the pinned snapshot outside the repository (the holdout
directory), with an embargo gap between the research window and the holdout for multi-bar labels,
and the research side must be unable to read it. Spec this before the Phase 1 lock.
