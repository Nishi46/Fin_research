"""Alpaca free-tier market-data client (daily bars, corporate actions).

Data only: nothing here can place an order. HTTP is injected so tests use recorded responses.
Endpoint paths and response shapes follow Alpaca's v2 market-data REST docs as remembered when
this was written; verify against the live docs before first real use.
"""

import json
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, date, datetime
from typing import Any, Protocol
from zoneinfo import ZoneInfo

from contracts.data import Bar, CorporateAction, CorporateActionKind
from harness.data.calendar import session_close_utc
from harness.data.common import SpinOff
from harness.data.config import DataConfig
from harness.data.ratelimit import RateLimiter

_NY = ZoneInfo("America/New_York")
HTTP_TIMEOUT_S = 30
PAGE_LIMIT = "10000"  # capital-literal-ok: API page size, not an account size
# The corporate-actions endpoint rejects limits above 1000 with HTTP 400 (bars allow 10000).
ACTIONS_PAGE_LIMIT = "1000"  # capital-literal-ok: API page size, not an account size
BARS_PATH = "/v2/stocks/bars"
MAX_PAGES = 10_000  # capital-literal-ok: runaway-paging guard, not an account size


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
    def __init__(
        self,
        http: HttpClient,
        config: DataConfig,
        limiter: RateLimiter,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._http = http
        self._config = config
        self._limiter = limiter
        self._clock = clock

    def _headers(self) -> dict[str, str]:
        return {
            "APCA-API-KEY-ID": self._config.api_key,
            "APCA-API-SECRET-KEY": self._config.api_secret,
        }

    def _pages(self, url: str, params: dict[str, str]) -> list[dict[str, Any]]:
        pages: list[dict[str, Any]] = []
        token: str | None = None
        seen: set[str] = set()
        while True:
            if len(pages) >= MAX_PAGES:
                raise RuntimeError(f"paging exceeded MAX_PAGES={MAX_PAGES} for {url}")
            query = dict(params)
            if token:
                query["page_token"] = token
            self._limiter.acquire()
            page = self._http.get_json(url, query, self._headers())
            pages.append(page)
            token = page.get("next_page_token")
            if not token:
                return pages
            if token in seen:
                raise RuntimeError(f"repeated next_page_token {token!r} from {url}")
            seen.add(token)

    def fetch_bars(self, symbols: Sequence[str], start: datetime, end: datetime) -> list[Bar]:
        # Never ask for today: its daily bar is still forming and would be stamped as a full
        # session close, and the SIP feed refuses queries ending within 15 minutes of now.
        today = self._clock().astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
        end = min(end, today)
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
            "limit": ACTIONS_PAGE_LIMIT,
        }
        actions: list[CorporateAction] = []
        for page in self._pages(
            self._config.data_url + self._config.corporate_actions_path, params
        ):
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

    def fetch_spin_offs(
        self, symbols: Sequence[str], start: datetime, end: datetime
    ) -> list[SpinOff]:
        params = {
            "symbols": ",".join(symbols),
            "types": "spin_off",
            "start": start.date().isoformat(),
            "end": end.date().isoformat(),
            "limit": ACTIONS_PAGE_LIMIT,
        }
        spin_offs: list[SpinOff] = []
        for page in self._pages(
            self._config.data_url + self._config.corporate_actions_path, params
        ):
            grouped = page.get("corporate_actions") or {}
            # Malformed rows raise on purpose: a silently dropped spin-off defeats the guard.
            for row in grouped.get("spin_offs", []):
                spin_offs.append(
                    SpinOff(
                        source_symbol=row["source_symbol"],
                        ex_date=date.fromisoformat(row["ex_date"]),
                        new_symbol=row["new_symbol"],
                        new_rate=float(row["new_rate"]),
                    )
                )
        return sorted(spin_offs, key=lambda s: (s.ex_date, s.source_symbol))
