"""Stooq and yfinance cross-checkers.

Reconciliation only: these classes expose `check()` and nothing that could serve as a primary
source. Neither vendor client is a dependency here, so each takes an injected fetcher; wiring a
real fetcher (and verifying the vendor's terms) is a later, explicit step.
"""

import csv
import io
from collections.abc import Callable, Mapping, Sequence
from datetime import date
from typing import Any

from contracts.data import Bar
from harness.data.calendar import session_close_utc
from harness.data.reconcile import ReconcileConfig, ReconciliationReport, reconcile

StooqFetcher = Callable[[str, date, date], str]
YFinanceFetcher = Callable[[str, date, date], Sequence[Mapping[str, Any]]]


def _range(primary: Sequence[Bar], symbol: str) -> tuple[date, date] | None:
    days = [b.ts.date() for b in primary if b.symbol == symbol]
    return (min(days), max(days)) if days else None


def _bar(symbol: str, row: Mapping[str, Any]) -> Bar:
    return Bar(
        symbol=symbol,
        ts=session_close_utc(date.fromisoformat(str(row["Date"])[:10])),
        open=float(row["Open"]),
        high=float(row["High"]),
        low=float(row["Low"]),
        close=float(row["Close"]),
        volume=float(row["Volume"]),
    )


def parse_stooq_csv(symbol: str, text: str) -> list[Bar]:
    if not text.lstrip().startswith("Date"):
        return []
    return [_bar(symbol, row) for row in csv.DictReader(io.StringIO(text))]


def _check(
    name: str,
    primary: Sequence[Bar],
    symbols: Sequence[str],
    config: ReconcileConfig | None,
    fetch: Callable[[str, date, date], list[Bar]],
) -> ReconciliationReport:
    other: list[Bar] = []
    wanted = set(symbols)
    for symbol in symbols:
        span = _range(primary, symbol)
        if span:
            other.extend(fetch(symbol, *span))
    return reconcile([b for b in primary if b.symbol in wanted], other, source=name, config=config)


class StooqCrossChecker:
    name = "stooq"

    def __init__(self, fetch_csv: StooqFetcher) -> None:
        self._fetch = fetch_csv

    def check(
        self,
        primary: Sequence[Bar],
        symbols: Sequence[str],
        config: ReconcileConfig | None = None,
    ) -> ReconciliationReport:
        return _check(
            self.name,
            primary,
            symbols,
            config,
            lambda s, a, b: parse_stooq_csv(s, self._fetch(s, a, b)),
        )


class YFinanceCrossChecker:
    name = "yfinance"

    def __init__(self, fetch_rows: YFinanceFetcher) -> None:
        self._fetch = fetch_rows

    def check(
        self,
        primary: Sequence[Bar],
        symbols: Sequence[str],
        config: ReconcileConfig | None = None,
    ) -> ReconciliationReport:
        return _check(
            self.name,
            primary,
            symbols,
            config,
            lambda s, a, b: [_bar(s, row) for row in self._fetch(s, a, b)],
        )
