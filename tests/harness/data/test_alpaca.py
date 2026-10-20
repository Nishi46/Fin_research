from collections.abc import Mapping
from datetime import UTC, date, datetime
from typing import Any

import pytest

from contracts.data import CorporateActionKind
from harness.data.alpaca import ACTIONS_PAGE_LIMIT, AlpacaDataClient, UrllibHttpClient
from harness.data.common import SpinOff
from harness.data.config import DataConfig
from harness.data.ratelimit import RateLimiter
from tests.data_fixtures import load_json


class RecordingHttp:
    def __init__(self, pages: list[dict[str, Any]]) -> None:
        self.pages = pages
        self.calls: list[tuple[str, Mapping[str, str], Mapping[str, str]]] = []

    def get_json(
        self, url: str, params: Mapping[str, str], headers: Mapping[str, str]
    ) -> dict[str, Any]:
        self.calls.append((url, dict(params), dict(headers)))
        return self.pages[len(self.calls) - 1]


def _config() -> DataConfig:
    return DataConfig.from_env(
        {
            "LAB_DATA_CACHE_DIR": "/cache",
            "ALPACA_API_KEY": "test-key-not-real",  # pragma: allowlist secret
            "ALPACA_API_SECRET": "test-secret-not-real",  # pragma: allowlist secret
        }
    )


def _client(http: RecordingHttp) -> AlpacaDataClient:
    limiter = RateLimiter(max_calls=1000, period_s=60.0, clock=lambda: 0.0, sleep=lambda s: None)
    return AlpacaDataClient(http, _config(), limiter)


def test_fetch_bars_follows_pagination_and_maps_to_close_time() -> None:
    http = RecordingHttp([load_json("alpaca_bars_page1.json"), load_json("alpaca_bars_page2.json")])
    bars = _client(http).fetch_bars(
        ["AAA"], datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 1, 8, tzinfo=UTC)
    )
    assert [b.close for b in bars] == [100.0, 100.5, 101.0]
    assert bars[0].ts == datetime(2026, 1, 5, 21, 0, tzinfo=UTC)
    assert len(http.calls) == 2
    assert http.calls[1][1]["page_token"] == "TOKEN2"


def test_request_uses_the_consolidated_sip_feed_raw_adjustment_and_key_headers() -> None:
    http = RecordingHttp([load_json("alpaca_bars_page2.json")])
    _client(http).fetch_bars(
        ["AAA"], datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 1, 8, tzinfo=UTC)
    )
    url, params, headers = http.calls[0]
    assert url.startswith("https://")
    assert params["feed"] == "sip"
    assert params["adjustment"] == "raw"
    assert params["timeframe"] == "1Day"
    assert headers["APCA-API-KEY-ID"] == "test-key-not-real"


def test_fetch_corporate_actions() -> None:
    http = RecordingHttp([load_json("alpaca_actions.json")])
    actions = _client(http).fetch_corporate_actions(
        ["SPL", "DIV"], datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 2, 1, tzinfo=UTC)
    )
    kinds = {(a.symbol, a.kind, a.value) for a in actions}
    assert ("SPL", CorporateActionKind.SPLIT, 2.0) in kinds
    assert ("DIV", CorporateActionKind.DIVIDEND, 1.0) in kinds


def test_limiter_is_used_per_request() -> None:
    sleeps: list[float] = []
    now = [0.0]

    def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)
        now[0] += seconds

    limiter = RateLimiter(max_calls=1, period_s=60.0, clock=lambda: now[0], sleep=fake_sleep)
    http = RecordingHttp([load_json("alpaca_bars_page1.json"), load_json("alpaca_bars_page2.json")])
    AlpacaDataClient(http, _config(), limiter).fetch_bars(
        ["AAA"], datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 1, 8, tzinfo=UTC)
    )
    assert sleeps == [60.0]


def test_config_requires_cache_dir_and_keys_and_hides_secrets() -> None:
    with pytest.raises(ValueError, match="LAB_DATA_CACHE_DIR"):
        DataConfig.from_env({"ALPACA_API_KEY": "k", "ALPACA_API_SECRET": "s"})
    with pytest.raises(ValueError, match="ALPACA_API_KEY"):
        DataConfig.from_env({"LAB_DATA_CACHE_DIR": "/c"})
    assert "test-secret-not-real" not in repr(_config())


def test_http_client_refuses_non_https() -> None:
    with pytest.raises(ValueError, match="https"):
        UrllibHttpClient().get_json("http://example.invalid/x", {}, {})


def test_corporate_actions_path_is_configurable_and_default_unchanged() -> None:
    base = {"LAB_DATA_CACHE_DIR": "/c", "ALPACA_API_KEY": "k", "ALPACA_API_SECRET": "s"}
    assert DataConfig.from_env(base).corporate_actions_path == "/v1/corporate-actions"
    custom = DataConfig.from_env(
        {**base, "ALPACA_CORPORATE_ACTIONS_PATH": "/v1beta1/corporate-actions"}
    )
    assert custom.corporate_actions_path == "/v1beta1/corporate-actions"
    with pytest.raises(ValueError, match="ALPACA_CORPORATE_ACTIONS_PATH"):
        DataConfig.from_env({**base, "ALPACA_CORPORATE_ACTIONS_PATH": "https://evil.example/x"})


def test_client_uses_the_configured_actions_path() -> None:
    import dataclasses

    http = RecordingHttp([load_json("alpaca_actions.json")])
    config = dataclasses.replace(_config(), corporate_actions_path="/v1beta1/corporate-actions")
    limiter = RateLimiter(max_calls=1000, period_s=60.0, clock=lambda: 0.0, sleep=lambda s: None)
    AlpacaDataClient(http, config, limiter).fetch_corporate_actions(
        ["SPL"], datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 2, 1, tzinfo=UTC)
    )
    assert http.calls[0][0].endswith("/v1beta1/corporate-actions")


# --- completed sessions only -----------------------------------------------------------------


def _clocked_client(
    http: RecordingHttp, now: datetime, env: dict[str, str] | None = None
) -> AlpacaDataClient:
    limiter = RateLimiter(max_calls=1000, period_s=60.0, clock=lambda: 0.0, sleep=lambda s: None)
    config = DataConfig.from_env(
        {
            "LAB_DATA_CACHE_DIR": "/cache",
            "ALPACA_API_KEY": "test-key-not-real",  # pragma: allowlist secret
            "ALPACA_API_SECRET": "test-secret-not-real",  # pragma: allowlist secret
            **(env or {}),
        }
    )
    return AlpacaDataClient(http, config, limiter, clock=lambda: now)


def test_end_is_capped_at_the_start_of_the_current_utc_day() -> None:
    # A request ending "now" would return today's partial, still-forming daily bar (stamped as a
    # full session close) and SIP refuses queries that end within 15 minutes of now.
    http = RecordingHttp([{"bars": {}}])
    now = datetime(2026, 10, 16, 15, 30, tzinfo=UTC)
    _clocked_client(http, now).fetch_bars(["AAA"], datetime(2026, 1, 1, tzinfo=UTC), now)
    assert http.calls[0][1]["end"] == "2026-10-16T00:00:00Z"


def test_an_earlier_end_is_left_alone() -> None:
    http = RecordingHttp([{"bars": {}}])
    now = datetime(2026, 10, 16, 15, 30, tzinfo=UTC)
    end = datetime(2026, 10, 9, 12, 0, tzinfo=UTC)
    _clocked_client(http, now).fetch_bars(["AAA"], datetime(2026, 1, 1, tzinfo=UTC), end)
    assert http.calls[0][1]["end"] == "2026-10-09T12:00:00Z"


def test_sip_feed_is_passed_through_when_configured() -> None:
    http = RecordingHttp([{"bars": {}}])
    now = datetime(2026, 10, 16, 15, 30, tzinfo=UTC)
    client = _clocked_client(http, now, {"ALPACA_DATA_FEED": "sip"})
    client.fetch_bars(["AAA"], datetime(2026, 1, 1, tzinfo=UTC), now)
    assert http.calls[0][1]["feed"] == "sip"


def test_iex_stays_selectable_through_the_environment() -> None:
    http = RecordingHttp([{"bars": {}}])
    now = datetime(2026, 10, 16, 15, 30, tzinfo=UTC)
    client = _clocked_client(http, now, {"ALPACA_DATA_FEED": "iex"})
    client.fetch_bars(["AAA"], datetime(2026, 1, 1, tzinfo=UTC), now)
    assert http.calls[0][1]["feed"] == "iex"


_SPIN_START = datetime(2025, 1, 1, tzinfo=UTC)
_SPIN_END = datetime(2026, 6, 30, tzinfo=UTC)


def _spin_offs(http: RecordingHttp, symbols: list[str] | None = None) -> list[SpinOff]:
    return _client(http).fetch_spin_offs(symbols or ["PARENT"], _SPIN_START, _SPIN_END)


def test_fetch_spin_offs_request_params() -> None:
    http = RecordingHttp([load_json("alpaca_spin_offs.json")["empty"]])
    _spin_offs(http, ["PARENT", "MOMCO"])
    url, params, _ = http.calls[0]
    assert url == _config().data_url + _config().corporate_actions_path
    assert params == {
        "symbols": "PARENT,MOMCO",
        "types": "spin_off",
        "start": "2025-01-01",
        "end": "2026-06-30",
        "limit": ACTIONS_PAGE_LIMIT,
    }


def test_fetch_spin_offs_parses_rows_sorted_by_ex_date_then_symbol() -> None:
    http = RecordingHttp([load_json("alpaca_spin_offs.json")["multi_row"]])
    assert _spin_offs(http) == [
        SpinOff("AAAP", date(2025, 4, 1), "AAAC", 0.2),
        SpinOff("GE", date(2025, 4, 1), "GEV", 1.0),
        SpinOff("HON", date(2025, 10, 30), "SOLS", 0.25),
    ]


def test_fetch_spin_offs_follows_pagination() -> None:
    fixture = load_json("alpaca_spin_offs.json")
    http = RecordingHttp([fixture["page1"], fixture["page2"]])
    assert _spin_offs(http) == [
        SpinOff("MOMCO", date(2026, 1, 15), "KIDCO", 0.25),
        SpinOff("PARENT", date(2026, 3, 10), "CHILD", 0.5),
    ]
    assert len(http.calls) == 2
    assert http.calls[1][1]["page_token"] == "TOKEN2"


def test_fetch_spin_offs_empty_or_missing_group_returns_empty_list() -> None:
    assert _spin_offs(RecordingHttp([load_json("alpaca_spin_offs.json")["empty"]])) == []
    assert _spin_offs(RecordingHttp([{}])) == []


def test_fetch_spin_offs_malformed_row_raises() -> None:
    http = RecordingHttp([load_json("alpaca_spin_offs.json")["malformed"]])
    with pytest.raises(KeyError):
        _spin_offs(http)
