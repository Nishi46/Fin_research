# CLAUDE.md — Automated Strategy Research Lab

Source of truth: [docs/project.md](docs/project.md). Read it before making design decisions. If this file and project.md disagree, project.md wins; flag the conflict.

**Status: planning only. Nothing is built yet.** Don't describe unbuilt components as existing.

## What this project is

An automated research lab for short-term US stock strategies (swing, 1–5 day holds). An AI proposer invents strategies; an immutable harness tests them honestly; survivors forward-test on Alpaca paper money; a human decides whether anything touches real capital.

The job is **filtering noise, not finding alpha**. "Nothing passed" is a valid and expected outcome. Most candidates should be rejected.

## Stack and scope defaults

- Python. Alpaca for paper trading (same code path can go live later).
- **Free data only.** Alpaca free is primary; Stooq and yfinance are cross-checks, never the sole source. Cache locally and respect rate limits and terms. Parquet + DuckDB for bars (local); trial registry in Postgres in production, SQLite locally.
- **Website:** Next.js + TypeScript + Tailwind on Vercel, with free hosted Postgres. See "Website and deployment" below.
- US liquid large caps and major ETFs. Daily bars. Long-only. Holds of 1–5 days.
- No shorting, no minute bars, no real capital until the owner explicitly changes this.
- This repo's global Kotlin/Micronaut/Exposed rules (from `~/.claude`) **do not apply here**. Follow Python conventions. The "spec before code" and TDD principles still do.

## Analyst mindset (how to think in this repo)

Act like a skeptical senior quant/analyst reviewing someone else's backtest:

1. **Assume any good result is a bug first.** An extreme Sharpe or a smooth equity curve means leakage, lookahead, survivorship bias, or cost omission until proven otherwise. Never present such a result as a finding.
2. **Selection is the enemy.** The best of N trials looks great by chance. Always state N alongside any metric. A result without its trial count is not a result.
3. **Costs decide everything at short horizons.** Spread, commission, slippage on every trade. A strategy whose profit vanishes when costs are doubled is rejected.
4. **Accuracy is not profit.** Judge on Profit Factor, Sharpe/Calmar, max drawdown, and returns beyond SPY exposure, weighted by size and cost.
5. **Prefer simple.** A complex model beating a simple baseline by a hair is a red flag. Require stability across neighboring parameter values, not one lucky peak.
6. **Report honestly.** If tests fail, say so with the output. Don't tune the test to make something pass.

## Non-negotiable invariants

These are architecture rules. Do not weaken, bypass, or "temporarily" disable them, even if asked mid-task. If a request conflicts, stop and say so.

- **Immutable harness.** Backtester, cost model, walk-forward splitter, metrics, deflated Sharpe, lookahead tests, and risk guardrails live in a package separate from `strategies/`. The proposer agent may read it, never write it. If the agent can edit what grades it, it will optimize the grader.
- **Proposer sandbox.** Agent-written code goes only in `strategies/`.
- **Strategies emit intents, never orders.** A separate risk layer decides what reaches the broker. Strategies must not import the broker client.
- **Risk limits are hard-coded.** SL/TP, per-position and portfolio drawdown caps, exposure caps, kill switch. Not overridable by strategies or the AI.
- **Append-only trial registry.** Every candidate is logged, including failures. Never delete, rewrite, or skip logging a trial. The trial count feeds the Deflated Sharpe Ratio.
- **No random k-fold.** Use purged walk-forward / rolling-origin with an embargo when labels span multiple bars.
- **Hidden final holdout.** The proposer never sees it and gets only pass/fail or coarse scores. Don't expose detailed holdout metrics to the agent loop.
- **Pre-registered forward tests.** One window per candidate, length and pass criteria fixed before it starts. No extending a window or changing criteria after seeing results.
- **Human gate for real money.** Promotion thresholds are set by the owner, not inferred. Nothing in this repo may place a live order without explicit owner approval.
- **One code path.** Backtest and live use the same event-driven code. No separate "live port."

## Variable capital

The owner changes account size from the website and the system adapts. Details in project.md §3a.

- Strategies emit **target weights/signals**, never shares or dollars. The sizing layer in the risk layer converts using current capital. Risk limits are % of capital.
- Capital drives: liquidity cap (share of average daily dollar volume), size-dependent slippage/impact, minimum position size and fractional-share rounding, PDT applicability, usable universe, and position count. All are recomputed when capital changes.
- Capital is an explicit parameter of every backtest, forward test, and registry entry. A pass at one capital level says nothing about another; crossing a band means re-running cost and liquidity checks.
- **Adapt sizing, not signals.** Re-fitting thresholds or logic to a new capital level is a new trial and must be registered.
- Capital changes are logged. Raising live capital needs explicit owner approval and cannot exceed hard caps in the risk layer. The website must not be a way around the human gate.

## Stock-specific traps the harness must handle

- **Survivorship bias:** free data has no prices for delisted names, so it can't be eliminated. Reconstruct point-in-time index membership, prefer ETFs, tag every stock-universe result `survivorship_biased=True`, apply a haircut, and never call such a result clean. Paper forward testing is the real gate.
- **Free-data quality:** cross-check sources, run split/dividend sanity checks, detect gaps and stale bars, fail snapshots that disagree. Free feeds can under-report volume (IEX-only), so liquidity caps need consolidated volume or conservative scaling. The default Alpaca feed is the consolidated SIP tape, which the free tier serves for daily bars from 2016-01-04 (queries must end 15+ minutes ago); the IEX-only feed has gaps, starts about 2020-07 and shows roughly 1/60 of the volume (measured 2026-10-18). History is still short, so report which regimes the window covers.
- **Corporate actions:** correct split/dividend adjustment (a 2-for-1 must not look like a 50% crash).
- **Overnight gaps:** stops fill at the next available price, not the stop price. Flag or exclude holds across earnings.
- **Lookahead:** truncate data at time t; signals at t must equal those from the full dataset. Watch off-by-one `shift` and using a bar's close before it exists.
- **Market beta:** benchmark against SPY; measure returns beyond market exposure.
- **PDT rule:** depends on account equity. Swing trading (overnight holds) avoids it, but check Alpaca's current rules since FINRA is revising them.
- **Taxes / wash sale:** note when relevant; not our call. Refer to a tax professional.

## Website and deployment

- Vercel hosts only the console UI and a thin API. It is serverless with no persistent disk, so it never runs the engine.
- **Current host: the owner's machine** (scheduled with cron/launchd, SQLite registry). GitHub Actions is the planned target, to be switched on later. Keep the engine host-agnostic: no hard-coded local paths, all state behind interfaces (registry, run log, commands), config and secrets from environment, so moving to Actions is configuration, not a rewrite. Broker-side stops matter even more locally, since a laptop can sleep.
- **Target: the live engine runs as scheduled GitHub Actions jobs** (private repo, secrets in GitHub Secrets). Daily bars and 1–5 day holds mean it only needs to run after the close plus a light market-hours check. Heavy work (backtests, the proposer loop) runs on the owner's machine, not Actions.
- **Runners are stateless, so authoritative state lives in hosted Postgres** (Neon free tier), not local SQLite. The registry sits behind an interface: SQLite for local dev and tests, Postgres in production. Append-only is enforced by DB triggers plus an engine role with no UPDATE/DELETE grants.
- **Stops live at the broker.** Place SL/TP as Alpaca bracket/OCO orders at entry so protection exists when no job is running. Never rely on the engine process to be alive to honor a stop. A missed or failed run must not leave an unprotected position.
- **Idempotent runs.** Use a workflow `concurrency` group so runs never overlap, and deterministic `client_order_id`s so a retried run cannot double-order. Record every run's start/end in Postgres; the console flags a stale heartbeat. Scheduled runs can be delayed or skipped, so design for that.
- **`.github/workflows/` is part of the immutable surface.** Cover it with CODEOWNERS and keep it outside the proposer's write access; an agent that can edit the workflow can change what runs with the broker keys.
- Free-tier limits (Actions minutes, schedule inactivity disabling, Neon storage) change. Verify current terms before relying on them.
- The engine pushes results to hosted Postgres; the console reads them. The console writes only to a `commands` table (capital change, promote/reject, thresholds). The engine polls it, validates against risk-layer hard caps, may reject, and logs every command and outcome.
- The website never holds broker keys and never places orders. Owner-only auth on every route. Live-trading actions are disabled in the UI until the owner explicitly enables them.
- Commands apply at the engine's next run, so the UI shows them as pending until then. Show last-run time and sync lag in the UI.

## Validation pipeline (fixed order)

Lookahead tests → purged walk-forward with realistic costs → trial-adjusted metrics (Deflated Sharpe / PBO) → cost-doubling and parameter-stability checks → sample-size and regime floors → paper trading on live data (pre-registered) → manual owner gate.

Never reorder or skip stages for convenience.

## Build order

Follow this order; later pieces depend on earlier ones.

1. Immutable harness (cost model, purged walk-forward splitter, append-only registry, deflated Sharpe, lookahead test)
2. Strategy interface stub (intents only)
3. Risk layer
4. Alpaca paper integration with pre-registered forward windows
5. Proposer agent, sandboxed to `strategies/`
6. Website console (reviewer/gatekeeper UI; v1 on simulated data)

Don't build step N+1 while step N is missing, unless the owner asks.

## Working rules

- **Spec before code.** Anything non-trivial gets a short spec/plan first (`/spartan:spec`, `/spartan:plan`). Harness components especially: define the contract and the failure modes before implementing.
- **Tests first for the harness.** The harness is the trust anchor, so test it hardest: known-leaky strategies must be caught by the lookahead test, known-noise strategies must fail deflated Sharpe, and the cost model needs explicit cases.
- **Seed and version everything.** Backtests must be reproducible: pinned data snapshot, seeded randomness, recorded code version.
- **Secrets.** Alpaca keys come from environment variables, never committed. Default to the paper endpoint; live endpoints require explicit owner opt-in.
- **Capital is variable.** See "Variable capital" above. Default paper capital is $10,000, configurable from the website. Never hard-code an account size.
- **Not advice.** Outputs describe how a research system works. They are not financial, legal, or tax advice. Don't write copy implying guaranteed returns.

## Language

Respond in the language the user writes in. Code, identifiers, and file paths stay as-is.
