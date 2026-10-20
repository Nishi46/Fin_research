# Automated Strategy Research Lab (short-term US stock trading)

Status: planning only. Nothing is built yet.

## 1. What this is

An automated research lab for trading strategies, with a human holding the key to the money.

An AI agent invents trading strategies (new signals, models, thresholds, entry/exit logic, and direct code changes, not just parameter sweeps). The system tests each one with honest, leak-free methods, then forward-tests survivors on live data with paper money. A human reviewer decides whether anything ever touches real capital. A separate risk layer enforces hard limits that neither the AI nor any strategy can override.

Human role: reviewer and gatekeeper, not operator.

### What it produces
A small number of vetted candidate strategies, each with a record of how it was tested and how many other attempts it was selected from. Most attempts are rejected by design. "Nothing passed" is a legitimate outcome.

### What it does not do
- It does not find a guaranteed edge. It filters out noise, which is a more modest claim.
- It does not decide to trade real money on its own.
- It does not replace human judgment on systemic risk.
- It is not for long-term investing (e.g. buy-and-hold index funds). It targets short-horizon patterns.

## 2. Vocabulary

A **trading strategy** is a set of rules that decides what to trade, when to enter, when to exit, and how much to put in, so a computer can follow it with no judgment calls.

Example: "Each day, look at the 500 largest US stocks. Buy the 20 that have fallen the most over the past week, hold three days, then sell. Never put more than 5% of the account in one stock."

The four questions every strategy answers:
1. What to trade (instruments)
2. When to enter
3. When to exit (take-profit, stop-loss, time stop)
4. How much (position sizing)

## 3. Goals and scope

- Goal: short-term trading (holds of roughly 1 to 5 days, swing style).
- Market: US stocks. Liquid large caps and major ETFs.
- Bars: daily to start (hourly later, minute bars only with better infrastructure and cost modeling).
- Direction: long-only to start (short selling adds borrow fees and availability limits).
- Language: Python.
- Broker / paper trading: Alpaca (free paper API, same code path can go live later).
- Data: free sources only. Alpaca free is primary (adjusted daily bars); Stooq and yfinance are cross-checks only. Free data has no prices for delisted names, so survivorship bias cannot be fully removed (see section 8). A paid point-in-time dataset is a possible later upgrade behind the same data interface, not part of this build.
- Interface: a website the owner can interact with, hosted on Vercel (see section 10).

Capital is a runtime setting, not a constant. The owner changes it from the website and the system adapts (see section 3a). Nothing in strategy code may hard-code an account size. Default paper capital is $10,000.

## 3a. Variable capital

Capital changes what is tradable and what it costs, so it is an input to the whole pipeline.

- **Strategies output target weights / signals, not shares or dollars.** A sizing layer in the risk layer converts weights to orders using current capital. Risk limits are percentages of capital (per-position cap, drawdown caps, exposure caps).
- **Capital-dependent constraints, recomputed whenever capital changes:**
  - Liquidity cap: position size limited to a small fraction of average daily dollar volume, so a larger account shrinks the usable universe and position count.
  - Market impact / slippage: cost model includes a size-dependent term, so the same strategy costs more per trade at larger capital.
  - Minimum position size and rounding: small accounts use fractional shares where available; positions too small to be worth the fixed costs are dropped, so a small account holds fewer names.
  - PDT: whether the pattern day trader rule applies depends on account equity (see the PDT section). Swing holds avoid it either way.
- **Validation is capital-aware.** Backtests and forward tests run at a stated capital level. A pass at one level does not carry to another. Moving to a different capital band triggers a re-run of cost-sensitivity and liquidity checks before the strategy is eligible at that size.
- **Strategy logic is not re-tuned per capital level.** What adapts is sizing, position count, universe filter, and cost assumptions. Re-fitting signals or thresholds to a new capital level is a new trial and goes in the registry.
- **Capital changes are gated.** On paper, changing capital takes effect immediately and is logged. For anything live, raising capital is a promotion-gate action requiring explicit owner approval, and cannot exceed hard caps set in the risk layer.

## 4. What is automated

- **Strategy search.** AI proposes variations: different models, features, thresholds, entry/exit logic.
- **Code changes.** When a strategy needs a different implementation, the AI edits code directly.
- **Validation loop.** Every candidate goes through walk-forward / rolling-origin testing, then real-time forward validation. No k-fold random splits (they leak the future into training and produce a fake edge).
- **Guardrails.** Hard SL/TP limits and drawdown caps, fixed in code, not overridable by the AI or any strategy.

## 5. What stays human / hard-coded

- Whether a strategy goes from validation to live capital. The threshold is set by the owner, not inferred by the AI.
- Anything where the failure mode is catastrophic and hard to observe quickly (e.g. correlated blowup across positions). Automation is good at "test 200 variants overnight" and bad at "notice the one systemic risk none of them tested for."
- Overfitting discipline and the real-money gate are hard-coded rules, not delegated to the AI's judgment.

## 6. Architecture

1. **Proposer agent.** Generates strategy variants and edits code, but only inside a sandboxed `strategies/` directory.
2. **Immutable harness.** Backtester, cost model, walk-forward splitter, metrics, and risk guardrails live in a separate package the agent can only read. Enforce with filesystem permissions or a separate repo with code owners. If the agent can edit what grades it, it will eventually optimize the grader.
3. **Validation pipeline.** Fixed stages in order: lookahead tests, purged walk-forward, trial-adjusted metrics, paper trading on live data, then the manual gate.
4. **Risk layer.** A separate process between strategy and broker that enforces SL/TP, per-position and portfolio drawdown caps, and a kill switch. Strategies emit intents; this layer decides what is actually sent.

### The loop
1. Agent invents a strategy.
2. System backtests it with walk-forward splits, realistic costs, and lookahead checks.
3. Survivors run on live data with paper money for a fixed, pre-registered period.
4. Passing strategies are flagged to the human.
5. Human decides whether it ever touches real capital.
6. Live strategies run under the risk layer.
7. Agent goes back to step 1.

## 7. Fighting overfitting (the actual job)

An AI iterating fast on backtests will find "genius" strategies that are noise fit to history.

- **Metrics that match the goal.** Profit Factor, Sharpe/Calmar, max drawdown, not accuracy. A model can be 60% directionally correct and still lose money once weighted by size and cost.
- **Suspicion of anything too good.** An extreme edge out of nowhere means leakage or lookahead bias first, not alpha.
- **Real-time validation is the actual gate**, not the backtest. The backtest only says a strategy isn't obviously broken. Forward testing shows whether it survives data it has never seen, including regime shifts.
- **Complexity penalties.** A model with 40 hyperparameters beating a simple one by a hair on Sharpe is a red flag, not a win.

### Tightening measures
- **Count every trial.** Multiple testing is the real overfitting engine: try 5,000 variants and the best Sharpe is impressive by chance. Log every candidate to an append-only registry and compute the Deflated Sharpe Ratio (or probability of backtest overfitting via CSCV) using that trial count. The AI cannot quietly discard failed attempts.
- **Pre-register forward tests.** Forward validation has the same selection problem. Each candidate gets one forward window with fixed length and fixed pass criteria set before it starts, and only a small number of finalists enter.
- **Hidden final holdout.** A data slice behind the harness that the proposer never sees. It only gets pass/fail or coarse scores, so it cannot learn the holdout through detailed feedback over many iterations.
- **Automated lookahead detection.** Since the AI edits code, it can introduce leakage by accident (off-by-one `shift`, using a bar's close before it exists). Test: truncate data at time t and confirm signals at t match those from the full dataset. Use the same event-driven code path for backtest and live so there is no separate "live port" that behaves differently.
- **Walk-forward details.** Purging and an embargo around split boundaries when labels span multiple bars. Include realistic fees, spread, slippage, and latency.
- **Mechanical complexity penalty.** Cap parameters/features; require beating a simple baseline by a margin that grows with complexity. Require stability: performance should hold across neighboring parameter values, not sit on one lucky peak.
- **Sample-size and regime floors.** Minimum number of independent trades and coverage of several distinct regimes, so a strategy that made all its money in one trending month cannot pass.

## 8. Short-term and US-stock specifics

### Costs
- Costs decide whether anything works at short horizons. Spread, commissions, and slippage hit every trade; 0.05% per trade pre-cost can easily lose money after costs.
- Use a conservative cost model. Reject any strategy whose profit disappears when assumed costs are doubled.
- Backtests assume you get the price you saw; real fills are worse, especially in fast or thin markets. Paper fills are usually too optimistic, so live results are the real test.
- Trade liquid names only so your own orders do not move the price.

### Stock-specific traps the harness must handle
- **Survivorship bias.** A universe of only today's companies hides every bankruptcy and delisting. With free data we cannot get prices for delisted names, so we mitigate rather than eliminate: reconstruct point-in-time index membership from free sources, prefer major ETFs (the cleaner signal), flag every stock-universe result as survivorship-biased in the registry and website, apply a conservative haircut, and treat paper forward testing as the real gate.
- **Free-data quality.** Cross-check adjusted prices between sources, run split/dividend sanity checks, detect gaps and stale bars, and fail a data snapshot if sources disagree beyond a threshold. Free feeds may under-report volume (e.g. IEX-only), so liquidity limits must use consolidated volume or a conservative scaling. Available history is limited (the consolidated SIP daily bars on Alpaca's free tier start 2016-01-04, measured 2026-10-18; the IEX-only feed starts about 2020-07 and has gaps), so the regime-coverage check must report which regimes the window actually contains.
- **Corporate actions.** Adjust splits and dividends correctly or a 2-for-1 split looks like a 50% crash.
- **Overnight gaps.** A -2% stop can fill at -8% after earnings. Model stops as filling at the next available price, not the stop price. Consider excluding or flagging holds across earnings dates.
- **Market exposure.** Most long strategies are partly "the market went up." Benchmark against SPY and measure returns beyond market exposure.
- **Short selling** costs more (borrow fees, availability), so start long-only.
- **Taxes.** US short-term gains are taxed as ordinary income, and the wash-sale rule can disallow losses when the same stock is rebought within 30 days. Both matter for high turnover. Ask a tax professional.

### Pattern day trader rule
The PDT rule has historically required $25,000 minimum equity in a margin account for frequent same-day round trips. FINRA has been revising it, so check the broker's current rules. The simple way around it is holding overnight (swing trading), which also suits daily bars.

## 9. Portfolio-level systemic risk (outside the search loop)

200 independent variants will not surface correlation risk, so handle it at the portfolio level:
- Cap aggregate exposure by factor, sector, and instrument.
- Run correlation and stress scenarios (gap moves, liquidity vanishing, exchange outage) against whatever is live.
- Monitor realized correlation between live strategies.
- Treat any divergence between live and paper fills as an alert; it is usually the first sign of a modeling gap.

## 10. Planned website (not built yet)

The owner wants this as a website to interact with, hosted on Vercel (Next.js, TypeScript, Tailwind). Vercel is serverless with no persistent disk, so it hosts only the console and a thin API. For now the engine runs on the owner's machine (cron/launchd, SQLite registry); the engine is kept host-agnostic so the move to Actions later is configuration, not a rewrite. The target is for the live engine (data pull, signals, risk layer, broker adapter) to run as scheduled GitHub Actions jobs in a private repo: daily bars and 1 to 5 day holds only need a run after the close plus a light market-hours check. Heavy work (backtests, the proposer loop) runs on the owner's machine. Because Actions runners keep no disk between runs, authoritative state (trial registry, run log, commands) lives in hosted Postgres; the registry sits behind an interface (SQLite locally, Postgres in production) and stays append-only via DB triggers and a restricted engine role.

Because the engine can be offline between runs, stop-loss and take-profit are placed at the broker as bracket/OCO orders at entry, so protection never depends on the engine being alive. Runs are idempotent (no overlapping runs, deterministic client order IDs), every run is logged, and a stale heartbeat is flagged in the console. The workflow files are part of the immutable surface (CODEOWNERS, no proposer write access). Free-tier limits change, so verify current terms.

- The engine writes results to a free hosted Postgres (Neon) that the console reads. The console writes only to a commands table (capital change, promote/reject, threshold edits).
- Each engine run polls that table, validates each command against the hard caps in the risk layer, and may reject it. Commands show as pending in the UI until the next run. Every command and outcome is logged.
- The site never holds broker keys and can never place an order. Access is owner-only (single-owner login). Live-trading actions stay disabled in the UI until the owner explicitly enables them.
- The console shows last-run time and sync lag so stale data is visible.

Planned as a reviewer/gatekeeper console:
- Pipeline funnel showing how many candidates pass each stage.
- Trial registry (every attempt, never deleted) with the running trial count.
- Candidate detail view: walk-forward results, cost-sensitivity, deflated Sharpe, complexity, stability.
- Guardrail panel showing the fixed risk limits (read-only).
- Promotion gate where the owner sets the thresholds and approves or rejects candidates.
- Capital control: the owner sets the account size; the console shows how the strategy adapts (position count, usable universe, estimated costs, PDT status) and flags candidates that need re-validation at the new size. Every change is logged.
- First version would run on simulated data so the workflow can be tried before the real harness exists.

## 11. Build order

1. **Immutable harness first** (everything depends on it): cost model, walk-forward splitter with purging/embargo, append-only trial registry, deflated Sharpe calculation, lookahead test.
2. Strategy interface stub: where the AI's strategies plug in, emitting intents only.
3. Risk layer: SL/TP, drawdown caps, kill switch, exposure caps.
4. Paper-trading integration (Alpaca) with pre-registered forward windows.
5. Proposer agent, sandboxed to `strategies/`.
6. Website console on top of the above.

## 12. First-version defaults

- Universe: liquid large-cap US stocks and major ETFs
- Bars: daily; holds of 1 to 5 days; long-only
- Python; Alpaca for paper trading; free data only; website on Vercel
- Paper trade for weeks or months before any real capital
- Capital is owner-configurable; default paper capital $10,000
- Live engine runs on scheduled GitHub Actions; stops are broker-side bracket orders
- When going live, size so that a total loss of the strategy's capital would not matter

## 13. Caveats

- Most automated strategy searches end with zero strategies that survive honest testing. Markets are competitive and real edges are small and fade.
- Passing tests reduces the chance of trading a fluke; it does not guarantee profit.
- Only risk what you can afford to lose.
- If the real goal is long-term savings growth, this is likely overkill. If the goal is the project itself (learning to build and validate automated strategies), it is a strong one, and paper trading allows that with no money at risk.
- This document describes how the system would work and is not financial, legal, or tax advice.