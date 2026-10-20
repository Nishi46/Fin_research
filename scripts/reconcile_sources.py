"""Compare Alpaca daily closes with yfinance and show how far apart they really are.

    uv run python -m scripts.reconcile_sources [--symbols SPY,AAPL] [--years 3]

Read-only: HTTP GETs to Alpaca (keys from the environment, like the smoke test) and to Yahoo via
yfinance (`uv add yfinance` first). Prints the reconcile verdict at the frozen tolerances and the
distribution of per-bar differences, which is the evidence for calibrating the three
`reconcile_*` thresholds. It changes no thresholds itself: re-freezing is the owner's call.

Basis: Alpaca bars are raw, so only SPLITS are applied to them here. yfinance `Close` with
auto_adjust=False is split-adjusted but not dividend-adjusted, so dividends are not applied.
"""

import argparse
import os
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

import numpy as np

from contracts.data import Bar, CorporateAction, CorporateActionKind
from harness.data.adjust import adjust_bars
from harness.data.alpaca import AlpacaDataClient, UrllibHttpClient
from harness.data.calendar import session_close_utc
from harness.data.config import DataConfig
from harness.data.ratelimit import RateLimiter
from harness.data.reconcile import ReconciliationReport, reconcile
from harness.data.yfinance_fetcher import make_yfinance_fetcher
from harness.thresholds import DEFAULT_THRESHOLDS

DEFAULT_SYMBOLS = ("SPY", "QQQ", "IWM", "AAPL", "MSFT", "NVDA", "AMZN", "JPM", "XOM", "TSLA")
DEFAULT_YEARS = 3
YAHOO_CALLS_PER_MINUTE = 20
PERCENTILES = (50.0, 90.0, 99.0, 99.9)
WORST_SHOWN = 10


@dataclass(frozen=True)
class Difference:
    symbol: str
    day: date
    relative: float


def relative_differences(primary: Sequence[Bar], other: Sequence[Bar]) -> list[Difference]:
    """|primary close / other close - 1| for every (symbol, day) present in both."""
    theirs = {(b.symbol, b.ts.date()): b for b in other}
    out: list[Difference] = []
    for bar in primary:
        match = theirs.get((bar.symbol, bar.ts.date()))
        if match is not None:
            out.append(Difference(bar.symbol, bar.ts.date(), abs(bar.close / match.close - 1.0)))
    return out


def _to_bar(symbol: str, row: Mapping[str, Any]) -> Bar:
    return Bar(
        symbol=symbol,
        ts=session_close_utc(date.fromisoformat(str(row["Date"])[:10])),
        open=float(row["Open"]),
        high=float(row["High"]),
        low=float(row["Low"]),
        close=float(row["Close"]),
        volume=float(row["Volume"]),
    )


def split_only(actions: Sequence[CorporateAction]) -> list[CorporateAction]:
    return [a for a in actions if a.kind is CorporateActionKind.SPLIT]


def summarize(diffs: Sequence[Difference]) -> list[str]:
    if not diffs:
        return ["no overlapping bars to summarize"]
    values = np.array([d.relative for d in diffs])
    lines = [f"bars compared: {len(diffs)}"]
    for p in PERCENTILES:
        lines.append(f"  p{p:g}: {np.percentile(values, p):.5f}")
    lines.append(f"  max: {values.max():.5f}")
    lines.append(f"worst {WORST_SHOWN}:")
    for d in sorted(diffs, key=lambda d: d.relative, reverse=True)[:WORST_SHOWN]:
        lines.append(f"  {d.symbol} {d.day} {d.relative:.5f}")
    return lines


def per_symbol_report(
    primary: Sequence[Bar], diffs: Sequence[Difference], *, tolerance: float
) -> list[str]:
    """One line per symbol: bars, first/last day, and how many differ beyond the tolerance."""
    days: dict[str, list[date]] = {}
    for bar in primary:
        days.setdefault(bar.symbol, []).append(bar.ts.date())
    bad: dict[str, int] = {}
    for d in diffs:
        if d.relative > tolerance:
            bad[d.symbol] = bad.get(d.symbol, 0) + 1
    return [
        f"{symbol}: {len(ds)} bars {min(ds)}..{max(ds)}, {bad.get(symbol, 0)} beyond tolerance"
        for symbol, ds in sorted(days.items())
    ]


def render_report(report: ReconciliationReport) -> list[str]:
    verdict = "PASS" if report.ok else f"FAIL ({report.reason})"
    return [
        f"{verdict}  {report.source}: compared {report.compared}/{report.primary_total}, "
        f"{len(report.disagreements)} beyond tolerance"
    ]


def run(env: Mapping[str, str], symbols: Sequence[str], years: int) -> int:
    config = DataConfig.from_env(env)
    alpaca = AlpacaDataClient(
        UrllibHttpClient(),
        config,
        RateLimiter(max_calls=config.calls_per_minute, period_s=60.0),
    )
    end = datetime.now(UTC)
    start = end - timedelta(days=365 * years)
    raw = alpaca.fetch_bars(symbols, start, end)
    actions = alpaca.fetch_corporate_actions(symbols, start, end)
    primary = adjust_bars(raw, split_only(actions))

    fetch = make_yfinance_fetcher(RateLimiter(max_calls=YAHOO_CALLS_PER_MINUTE, period_s=60.0))
    other: list[Bar] = []
    for symbol in symbols:
        days = [b.ts.date() for b in primary if b.symbol == symbol]
        if days:
            rows = fetch(symbol, min(days), max(days))
            other.extend(_to_bar(symbol, row) for row in rows)

    report = reconcile(primary, other, source="yfinance")
    for line in render_report(report):
        print(line)
    diffs = relative_differences(primary, other)
    for line in summarize(diffs):
        print(line)
    print("per symbol:")
    tolerance = DEFAULT_THRESHOLDS.reconcile_price_tolerance
    for line in per_symbol_report(primary, diffs, tolerance=tolerance):
        print(f"  {line}")
    return 0 if report.ok else 1


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--symbols", default=",".join(DEFAULT_SYMBOLS))
    parser.add_argument("--years", type=int, default=DEFAULT_YEARS)
    args = parser.parse_args(argv)
    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    try:
        return run(os.environ, symbols, args.years)
    except (ValueError, ModuleNotFoundError) as err:
        print(f"cannot run: {err}")
        return 2


if __name__ == "__main__":
    sys.exit(main())
