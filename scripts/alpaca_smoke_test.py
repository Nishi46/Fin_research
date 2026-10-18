"""Read-only Alpaca data smoke test. The OWNER runs this once with paper keys; never in CI.

    uv run python -m scripts.alpaca_smoke_test

Reads ALPACA_API_KEY and ALPACA_API_SECRET (via DataConfig.from_env). Sends only HTTP GETs to
the market-data host, never places an order, never prints or stores the keys (they are masked
in all output). At most 5 calls per check, far below the 200/min free-tier limit.
"""

import dataclasses
import json
import os
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from contracts.data import CorporateActionKind
from harness.data.alpaca import AlpacaDataClient
from harness.data.config import DataConfig
from harness.data.ratelimit import RateLimiter

CANDIDATE_PATHS = ("/v1/corporate-actions", "/v1beta1/corporate-actions")
MAX_CALLS_PER_CHECK = 5
MIN_DATA_AGE = timedelta(minutes=20)
BARS_LOOKBACK = timedelta(days=16)
HTTP_TIMEOUT_S = 30
EXPECTED_SPLIT_VALUE = 4.0
EXPECTED_SPLIT_DATE = "2020-08-31"
BARS_MARKER = "/v2/stocks/bars"


@dataclass(frozen=True)
class ProbeResponse:
    status: int
    body: dict[str, Any] | None
    headers: Mapping[str, str]


class ProbeClient(Protocol):
    def get(
        self, url: str, params: Mapping[str, str], headers: Mapping[str, str]
    ) -> ProbeResponse: ...


class UrllibProbeClient:
    """GET only. Returns the status instead of raising, so 404s can be reported."""

    def get(self, url: str, params: Mapping[str, str], headers: Mapping[str, str]) -> ProbeResponse:
        if not url.startswith("https://"):
            raise ValueError("only https URLs are allowed")
        full = f"{url}?{urllib.parse.urlencode(params)}"
        request = urllib.request.Request(full, headers=dict(headers), method="GET")
        try:
            with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT_S) as resp:
                return ProbeResponse(resp.status, _json(resp.read()), dict(resp.headers.items()))
        except urllib.error.HTTPError as err:
            return ProbeResponse(err.code, _json(err.read()), dict(err.headers.items()))


def _json(raw: bytes) -> dict[str, Any] | None:
    try:
        data = json.loads(raw)
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


class HttpStatusError(Exception):
    def __init__(self, status: int) -> None:
        super().__init__(f"HTTP {status}")
        self.status = status


class _Recorder:
    """Adapts a ProbeClient to the HttpClient the real parser uses. Keeps what was seen and
    returns only the first page, so a check can never loop past its call budget."""

    def __init__(self, probe: ProbeClient) -> None:
        self._probe = probe
        self.calls = 0
        self.raw_bodies: list[dict[str, Any]] = []
        self.response_headers: list[Mapping[str, str]] = []
        self.last_status = 0

    def get_json(
        self, url: str, params: Mapping[str, str], headers: Mapping[str, str]
    ) -> dict[str, Any]:
        if self.calls >= MAX_CALLS_PER_CHECK:
            raise RuntimeError("call budget exceeded")
        self.calls += 1
        response = self._probe.get(url, params, headers)
        self.last_status = response.status
        self.response_headers.append(response.headers)
        if response.status != 200 or response.body is None:
            raise HttpStatusError(response.status)
        self.raw_bodies.append(response.body)
        return {**response.body, "next_page_token": None}


class _Replay:
    def __init__(self, body: dict[str, Any]) -> None:
        self._body = {**body, "next_page_token": None}

    def get_json(
        self, url: str, params: Mapping[str, str], headers: Mapping[str, str]
    ) -> dict[str, Any]:
        return self._body


@dataclass
class CheckResult:
    name: str
    passed: bool
    evidence: list[str] = field(default_factory=list)


def _limiter() -> RateLimiter:
    return RateLimiter(max_calls=MAX_CALLS_PER_CHECK, period_s=60.0)


def _ratelimit_headers(headers: Mapping[str, str]) -> dict[str, str]:
    return {k: v for k, v in headers.items() if "ratelimit" in k.lower().replace("-", "")}


def _token_keys(body: Mapping[str, Any]) -> list[str]:
    return [k for k in body if "token" in k.lower() or "page" in k.lower()]


def check_bars(
    probe: ProbeClient, config: DataConfig, now: datetime
) -> tuple[CheckResult, CheckResult, list[Mapping[str, str]]]:
    """Returns (bars check, pagination check, response headers seen)."""
    recorder = _Recorder(probe)
    client = AlpacaDataClient(recorder, config, _limiter())
    end = now - MIN_DATA_AGE
    start = end - BARS_LOOKBACK
    try:
        bars = client.fetch_bars(["SPY"], start, end)
    except HttpStatusError as err:
        bars_result = CheckResult("bars", False, [f"SPY bars request returned HTTP {err.status}"])
        return bars_result, CheckResult("pagination", False, ["no bars response"]), []
    except (KeyError, ValueError, TypeError) as err:
        found = sorted(recorder.raw_bodies[0]) if recorder.raw_bodies else []
        return (
            CheckResult("bars", False, [f"parse failed: {err!r}; top-level keys {found}"]),
            CheckResult("pagination", False, ["no usable bars response"]),
            list(recorder.response_headers),
        )
    ok = len(bars) > 0
    evidence = [
        f"HTTP 200, adjustment=raw, feed={config.feed}",
        f"{len(bars)} bars parsed; first {bars[0].ts.date()} last {bars[-1].ts.date()}"
        if bars
        else "0 bars parsed",
    ]
    keys = _token_keys(recorder.raw_bodies[0])
    pagination = CheckResult(
        "pagination",
        bool(keys),
        [f"page/token keys present in bars response: {keys}" if keys else "no token key found"],
    )
    return CheckResult("bars", ok, evidence), pagination, list(recorder.response_headers)


@dataclass
class _Probe:
    path: str
    symbol: str
    status: int
    body: dict[str, Any] | None


def _probe_actions(
    probe: ProbeClient, config: DataConfig, path: str, symbol: str, start: datetime, end: datetime
) -> tuple[_Probe, list[Mapping[str, str]]]:
    recorder = _Recorder(probe)
    cfg = dataclasses.replace(config, corporate_actions_path=path)
    try:
        AlpacaDataClient(recorder, cfg, _limiter()).fetch_corporate_actions([symbol], start, end)
    except HttpStatusError as err:
        return _Probe(path, symbol, err.status, None), list(recorder.response_headers)
    except (KeyError, ValueError, TypeError):
        pass  # status was 200; parsing is judged separately from the raw body
    body = recorder.raw_bodies[0] if recorder.raw_bodies else None
    return _Probe(path, symbol, 200, body), list(recorder.response_headers)


def _row_fields(body: Mapping[str, Any], group: str) -> list[str]:
    rows = (body.get("corporate_actions") or {}).get(group) or []
    return sorted(rows[0]) if rows else []


def _parse(config: DataConfig, probe: _Probe) -> tuple[list[str], str | None]:
    """Run the REAL parser on a recorded body. Returns (rendered actions, error text)."""
    assert probe.body is not None
    client = AlpacaDataClient(_Replay(probe.body), config, _limiter())
    epoch = datetime(2000, 1, 1, tzinfo=UTC)
    try:
        actions = client.fetch_corporate_actions([probe.symbol], epoch, epoch)
    except (KeyError, ValueError, TypeError) as err:
        return [], repr(err)
    return [
        f"{a.symbol} {a.kind.value} value={a.value} effective={a.effective_at.date()}"
        for a in actions
    ], None


def check_corporate_actions(
    probe: ProbeClient, config: DataConfig, now: datetime
) -> tuple[list[CheckResult], str | None, list[Mapping[str, str]]]:
    probes: list[_Probe] = []
    headers: list[Mapping[str, str]] = []
    for path in CANDIDATE_PATHS:
        for symbol, start, end in (
            ("AAPL", datetime(2020, 8, 1, tzinfo=UTC), datetime(2020, 9, 30, tzinfo=UTC)),
            ("SPY", now - timedelta(days=365), now),
        ):
            result, seen = _probe_actions(probe, config, path, symbol, start, end)
            probes.append(result)
            headers.extend(seen)

    status_lines = [f"{p.path} {p.symbol}: HTTP {p.status}" for p in probes]
    working_paths = [
        p
        for p in dict.fromkeys(q.path for q in probes)
        if all(q.status == 200 for q in probes if q.path == p)
    ]
    # Prefer the configured path when it works, otherwise the first that does.
    ordered = sorted(working_paths, key=lambda p: p != config.corporate_actions_path)
    chosen = ordered[0] if ordered else None

    results: list[CheckResult] = [
        CheckResult(
            "actions_path_matches_config",
            chosen == config.corporate_actions_path,
            [*status_lines, f"configured path: {config.corporate_actions_path}"],
        )
    ]
    for symbol, label, kind, group, row_ok in (
        ("AAPL", "aapl_split_parse", CorporateActionKind.SPLIT, "forward_splits", _is_aapl_split),
        ("SPY", "spy_dividend_parse", CorporateActionKind.DIVIDEND, "cash_dividends", _is_div),
    ):
        results.append(_parse_check(config, probes, chosen, symbol, label, kind, group, row_ok))
    return results, chosen, headers


def _is_aapl_split(rendered: str) -> bool:
    return f"value={EXPECTED_SPLIT_VALUE} effective={EXPECTED_SPLIT_DATE}" in rendered


def _is_div(rendered: str) -> bool:
    return True


def _parse_check(
    config: DataConfig,
    probes: list[_Probe],
    chosen: str | None,
    symbol: str,
    label: str,
    kind: CorporateActionKind,
    group: str,
    row_ok: Callable[[str], bool],
) -> CheckResult:
    if chosen is None:
        return CheckResult(label, False, ["no corporate-actions path returned HTTP 200"])
    probe = next(p for p in probes if p.path == chosen and p.symbol == symbol)
    assert probe.body is not None
    body = probe.body
    groups = sorted(body.get("corporate_actions") or {})
    evidence = [
        f"path {chosen}: top-level keys {sorted(body)}",
        f"corporate_actions groups: {groups}",
        f"has {group}: {group in groups}",
        f"{group} row fields: {_row_fields(body, group)}",
    ]
    rendered, error = _parse(config, probe)
    if error:
        evidence.append(f"real parser failed: {error}")
        return CheckResult(label, False, evidence)
    wanted = [r for r in rendered if f" {kind.value} " in r]
    evidence.append(f"parsed: {wanted}")
    return CheckResult(label, bool(wanted) and any(row_ok(r) for r in wanted), evidence)


def _mask(text: str, secrets: list[str]) -> str:
    for secret in secrets:
        if secret:
            text = text.replace(secret, "***")
    return text


def run(
    probe: ProbeClient,
    config: DataConfig,
    now: datetime,
    out: Callable[[str], None] | None = None,
) -> int:
    secrets = [config.api_key, config.api_secret]

    def emit(text: str) -> None:
        masked = _mask(text, secrets)
        (out or print)(masked)

    bars, pagination, bar_headers = check_bars(probe, config, now)
    actions, chosen, action_headers = check_corporate_actions(probe, config, now)
    seen = [*bar_headers, *action_headers]
    limits: dict[str, str] = {}
    for headers in seen:
        limits.update(_ratelimit_headers(headers))
    rate = CheckResult(
        "rate_limit",
        True,
        [
            f"rate-limit headers: {limits}" if limits else "no rate-limit headers returned",
            f"calls per check capped at {MAX_CALLS_PER_CHECK}",
        ],
    )
    results = [bars, *actions, pagination, rate]
    for result in results:
        emit(f"{'PASS' if result.passed else 'FAIL'}  {result.name}")
        for line in result.evidence:
            emit(f"      {line}")
    failed = [r.name for r in results if not r.passed]
    if chosen is None:
        action = "no corporate-actions path worked; check the docs and the response above"
    elif chosen != config.corporate_actions_path:
        action = f"set ALPACA_CORPORATE_ACTIONS_PATH={chosen} (or change the default in config.py)"
    else:
        action = "none needed for the path"
    emit(
        f"SUMMARY: corporate-actions path that worked: {chosen or 'none'}; "
        f"failed checks: {failed or 'none'}; change needed: {action}"
    )
    return 0 if not failed else 1


def main() -> int:
    env = dict(os.environ)
    if not env.get("LAB_DATA_CACHE_DIR"):
        env["LAB_DATA_CACHE_DIR"] = tempfile.mkdtemp(prefix="smoke-cache-")
    try:
        config = DataConfig.from_env(env)
    except ValueError as err:
        print(f"cannot start: {err}")
        return 2
    return run(UrllibProbeClient(), config, datetime.now(UTC))


if __name__ == "__main__":
    sys.exit(main())
