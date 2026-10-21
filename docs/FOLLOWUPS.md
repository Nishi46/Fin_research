# Follow-ups

Decisions the owner has made that are not built yet. Each has a trigger, so none is forgotten.

## Independent review of the harness (2026-10-20)

Four reviewers, results and fixes in `docs/PHASE1_HARNESS_REVIEW.md` ("Independent review results").
Decided 2026-10-20: PBO is a field-level diagnostic (code message and docs say so); only
walk-forward survivors contribute a return series (spec R4a, R4b). Also decided and built the same
day: a cost-model fingerprint in the PBO group key; `run_pipeline` requires a `ResearchWindow`-wrapped
provider; the strategy interface (`ParametrizedStrategy`, spec `.planning/specs/strategy-interface.md`)
with a conformance suite, so build step 2 is complete. The one open item from the review is that
strategy code runs inside the grading process (see `docs/PROPOSER_ISOLATION.md`); it is the main
design item for build step 5 and does not block the Phase 1 lock.

## True PBO, before the proposer agent (build step 5): BUILT 2026-10-20, awaiting owner review

Spec `.planning/specs/true-pbo.md`, plan `.planning/plans/true-pbo.md`. Built in a session started
with `LAB_UNLOCK_PROTECTED=1`; nothing is committed (the owner commits). The precondition for
step 5 is met once the owner has reviewed and committed it.

What changed: `Registry` gained `append_returns` / `returns_for_group` and `ReturnSeries`
(contracts, fake, contract suite); a `trial_returns` table with append-only triggers in both DDL
files (the SQLite DDL is now idempotent so existing database files gain the table); thresholds
`pbo_min_trials = 30`, `pbo_max_peers = 200`, `pbo_n_blocks = 16`; `harness/validation/peers.py`;
stage 3 gates on cross-trial PBO once the group has 30 trials. The old neighbour PBO is kept as the
informational metric `pbo_neighbours`.

Measured and decided on the way (worth the owner's attention):
- **Only trials that reach stage 3 file a series.** About 40% of pure-noise submissions clear
  walk-forward, so a 30-trial group takes roughly 75 submissions. This is deliberate: CSCV ranks the
  winner relative to the field, so filing trials that fail walk-forward would let an agent dilute
  the field with junk and drive PBO toward zero. Trials rejected at lookahead are excluded for the
  same reason (a leaky series is unrealistically good). DSR still deflates by every logged trial.
- **PBO alone is a weak gate on pure noise.** The best of N noise strategies scattered between 0.27
  and 0.69 (mean about 0.57), so PBO rejects a given draw only a little over half the time. DSR is
  what reliably rejects best-of-N noise. A planted edge among 25 to 49 noise peers gave PBO 0.000
  on all six seeds, so the gate did not reject a genuine edge in the tests.
- **R7 runtime:** 16 blocks at 201 columns takes 0.23s; not a constraint. Storage is 3.8 KB per
  1,000 days after delta-encoding the timestamps (the spec estimated 8 KB).
- The group key omits the symbol list, as specified; adding it is a one-line change.
- Re-submitted identical code counts as extra peers (owner decision, no dedupe).

Not done: the optional `verify` command that recomputes a group's PBO from stored series.

Also written 2026-10-19: `docs/PROPOSER_ISOLATION.md` (design and runbook; not set up, needs the
owner's machine and sudo) and `docs/PHASE1_HARNESS_REVIEW.md` (reading guide for the lock).

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

Decided by the owner (the t-statistic bar was required before this section was rewritten; recorded
again here because a later audit could not find it outside the thresholds file):
- **Bar:** the gate requires a t-statistic of the holdout's net daily returns of at least 2
  (`holdout_min_t_stat = 2.0`), not a fixed Sharpe, because a t-statistic scales with holdout length.
- **Length and period (2026-10-18):** the most recent 2 years of data (`holdout_years = 2`, about 504 daily
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
