"""Alpaca free-tier market-data client (daily bars, corporate actions).

Data only: nothing here can place an order. HTTP is injected so tests use recorded responses.
Endpoint paths and response shapes follow Alpaca's v2 market-data REST docs as remembered when
this was written; verify against the live docs before first real use.
"""

import json
import urllib.parse
import urllib.request
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any, Protocol
from zoneinfo import ZoneInfo

from contracts.data import Bar, CorporateAction, CorporateActionKind
from harness.data.calendar import session_close_utc
from harness.data.config import DataConfig
from harness.data.ratelimit import RateLimiter

_NY = ZoneInfo("America/New_York")
HTTP_TIMEOUT_S = 30
PAGE_LIMIT = "10000"  # capital-literal-ok: API page size, not an account size
BARS_PATH = "/v2/stocks/bars"
ACTIONS_PATH = "/v1/corporate-actions"


class HttpClient(Protocol):
    def get_json(
        self, url: str, params: Mapping[str, str], headers: Mapping[str, str]
    ) -> dict[str, Any]: ...


class UrllibHttpClient:
    """Minimal real client. Never constructed or called by tests or at import time."""

    def get_json(
        self, url: str, params: Mapping[str, str], headers: Mapping[str, str]
    ) -> dict[str, Any]:
        if not url.startswith("https://"):
            raise ValueError("only https URLs are allowed")
        full = f"{url}?{urllib.parse.urlencode(params)}" if params else url
        request = urllib.request.Request(full, headers=dict(headers))
        with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT_S) as response:
            data = json.loads(response.read())
        if not isinstance(data, dict):
            raise ValueError("unexpected response shape")
        return data


def _iso(ts: datetime) -> str:
    return ts.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _ex_date(text: str) -> datetime:
    return datetime.fromisoformat(text).replace(tzinfo=UTC)


class AlpacaDataClient:
    def __init__(self, http: HttpClient, config: DataConfig, limiter: RateLimiter) -> None:
        self._http = http
        self._config = config
        self._limiter = limiter

    def _headers(self) -> dict[str, str]:
        return {
            "APCA-API-KEY-ID": self._config.api_key,
            "APCA-API-SECRET-KEY": self._config.api_secret,
        }

    def _pages(self, url: str, params: dict[str, str]) -> list[dict[str, Any]]:
        pages: list[dict[str, Any]] = []
        token: str | None = None
        while True:
            query = dict(params)
            if token:
                query["page_token"] = token
            self._limiter.acquire()
            page = self._http.get_json(url, query, self._headers())
            pages.append(page)
            token = page.get("next_page_token")
            if not token:
                return pages

    def fetch_bars(self, symbols: Sequence[str], start: datetime, end: datetime) -> list[Bar]:
        params = {
            "symbols": ",".join(symbols),
            "timeframe": "1Day",
            "start": _iso(start),
            "end": _iso(end),
            "adjustment": "raw",
            "feed": self._config.feed,
            "limit": PAGE_LIMIT,
        }
        bars: list[Bar] = []
        for page in self._pages(self._config.data_url + BARS_PATH, params):
            for symbol, rows in (page.get("bars") or {}).items():
                for row in rows:
                    stamp = datetime.fromisoformat(row["t"].replace("Z", "+00:00"))
                    bars.append(
                        Bar(
                            symbol=symbol,
                            ts=session_close_utc(stamp.astimezone(_NY).date()),
                            open=row["o"],
                            high=row["h"],
                            low=row["l"],
                            close=row["c"],
                            volume=row["v"],
                        )
                    )
        return sorted(bars, key=lambda b: (b.ts, b.symbol))

    def fetch_corporate_actions(
        self, symbols: Sequence[str], start: datetime, end: datetime
    ) -> list[CorporateAction]:
        params = {
            "symbols": ",".join(symbols),
            "types": "forward_split,reverse_split,cash_dividend",
            "start": start.date().isoformat(),
            "end": end.date().isoformat(),
            "limit": PAGE_LIMIT,
        }
        actions: list[CorporateAction] = []
        for page in self._pages(self._config.data_url + ACTIONS_PATH, params):
            grouped = page.get("corporate_actions") or {}
            for row in [*grouped.get("forward_splits", []), *grouped.get("reverse_splits", [])]:
                actions.append(
                    CorporateAction(
                        symbol=row["symbol"],
                        kind=CorporateActionKind.SPLIT,
                        effective_at=_ex_date(row["ex_date"]),
                        value=row["new_rate"] / row["old_rate"],
                    )
                )
            for row in grouped.get("cash_dividends", []):
                actions.append(
                    CorporateAction(
                        symbol=row["symbol"],
                        kind=CorporateActionKind.DIVIDEND,
                        effective_at=_ex_date(row["ex_date"]),
                        value=row["rate"],
                    )
                )
        return sorted(actions, key=lambda a: (a.effective_at, a.symbol))
