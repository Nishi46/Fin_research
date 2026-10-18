from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

import pytest

from harness.data.config import DataConfig
from scripts.alpaca_smoke_test import ProbeResponse, run
from tests.data_fixtures import load_json

KEY = "SMOKE-KEY-DO-NOT-LEAK"  # pragma: allowlist secret
SECRET = "SMOKE-SECRET-DO-NOT-LEAK"  # pragma: allowlist secret
NOW = datetime(2026, 10, 15, 18, 0, tzinfo=UTC)
GOOD_PATH = "/v1beta1/corporate-actions"
OLD_PATH = "/v1/corporate-actions"


class FakeProbe:
    def __init__(
        self,
        working: set[str],
        *,
        aapl: str = "smoke_actions_aapl.json",
        spy: str = "smoke_actions_spy.json",
        headers: Mapping[str, str] | None = None,
    ) -> None:
        self.working = working
        self.aapl = aapl
        self.spy = spy
        self.headers = dict(headers or {"x-ratelimit-remaining": "199"})
        self.calls: list[tuple[str, dict[str, str]]] = []
        self.sent_headers: list[Mapping[str, str]] = []

    def get(self, url: str, params: Mapping[str, str], headers: Mapping[str, str]) -> ProbeResponse:
        self.calls.append((url, dict(params)))
        self.sent_headers.append(headers)
        if "/v2/stocks/bars" in url:
            return ProbeResponse(200, load_json("smoke_bars_spy.json"), self.headers)
        path = url.removeprefix("https://data.alpaca.markets")
        if path not in self.working:
            return ProbeResponse(404, None, self.headers)
        name = self.aapl if params["symbols"] == "AAPL" else self.spy
        body: dict[str, Any] = load_json(name)
        return ProbeResponse(200, body, self.headers)


def _config() -> DataConfig:
    return DataConfig.from_env(
        {"LAB_DATA_CACHE_DIR": "/c", "ALPACA_API_KEY": KEY, "ALPACA_API_SECRET": SECRET}
    )


def _run(probe: FakeProbe, capsys: pytest.CaptureFixture[str]) -> tuple[int, str]:
    code = run(probe, _config(), NOW)
    return code, capsys.readouterr().out


def test_good_run_passes_and_reports_working_path(capsys: pytest.CaptureFixture[str]) -> None:
    code, out = _run(FakeProbe({OLD_PATH, GOOD_PATH}), capsys)
    assert code == 0
    assert "FAIL" not in out
    assert "PASS" in out
    assert "value=4.0" in out
    assert "2020-08-31" in out
    assert "x-ratelimit-remaining" in out
    assert "SUMMARY" in out


def test_wrong_default_path_404_and_right_path_200_is_reported(
    capsys: pytest.CaptureFixture[str],
) -> None:
    code, out = _run(FakeProbe({GOOD_PATH}), capsys)
    summary = out[out.index("SUMMARY") :]
    assert GOOD_PATH in summary
    assert "ALPACA_CORPORATE_ACTIONS_PATH=" + GOOD_PATH in summary
    assert "404" in out
    assert code == 1  # the configured (default) path failed, so not every check passed


def test_unexpected_field_names_fail_with_the_names_found(
    capsys: pytest.CaptureFixture[str],
) -> None:
    probe = FakeProbe(
        {OLD_PATH}, aapl="smoke_actions_bad_fields.json", spy="smoke_actions_bad_fields.json"
    )
    code, out = _run(probe, capsys)
    assert code == 1
    assert "FAIL" in out
    assert "exDate" in out
    assert "ratio" in out
    assert "amount" in out


def test_no_path_works(capsys: pytest.CaptureFixture[str]) -> None:
    code, out = _run(FakeProbe(set()), capsys)
    assert code == 1
    assert "no corporate-actions path" in out


def test_keys_never_appear_in_output(capsys: pytest.CaptureFixture[str]) -> None:
    probe = FakeProbe({OLD_PATH, GOOD_PATH}, headers={"x-ratelimit-note": f"echo {KEY} {SECRET}"})
    for working in ({OLD_PATH, GOOD_PATH}, {GOOD_PATH}, set()):
        probe.working = working
        _, out = _run(probe, capsys)
        assert KEY not in out
        assert SECRET not in out
    assert any(h.get("APCA-API-KEY-ID") == KEY for h in probe.sent_headers)  # sent, not printed


def test_call_budget_and_get_only(capsys: pytest.CaptureFixture[str]) -> None:
    probe = FakeProbe({OLD_PATH, GOOD_PATH})
    _run(probe, capsys)
    bars_calls = [c for c in probe.calls if "/v2/stocks/bars" in c[0]]
    action_calls = [c for c in probe.calls if "/v2/stocks/bars" not in c[0]]
    assert len(bars_calls) <= 5
    assert len(action_calls) <= 5
    assert {c[0].removeprefix("https://data.alpaca.markets") for c in action_calls} == {
        OLD_PATH,
        GOOD_PATH,
    }


def test_bars_request_is_raw_and_ends_at_least_20_minutes_ago(
    capsys: pytest.CaptureFixture[str],
) -> None:
    probe = FakeProbe({OLD_PATH})
    _run(probe, capsys)
    _, params = next(c for c in probe.calls if "/v2/stocks/bars" in c[0])
    assert params["adjustment"] == "raw"
    assert params["symbols"] == "SPY"
    end = datetime.fromisoformat(params["end"].replace("Z", "+00:00"))
    assert (NOW - end).total_seconds() >= 20 * 60


def test_pagination_token_key_is_reported(capsys: pytest.CaptureFixture[str]) -> None:
    _, out = _run(FakeProbe({OLD_PATH}), capsys)
    assert "next_page_token" in out


def test_main_exit_code_without_keys(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from scripts.alpaca_smoke_test import main

    monkeypatch.delenv("ALPACA_API_KEY", raising=False)
    monkeypatch.delenv("ALPACA_API_SECRET", raising=False)
    assert main() == 2
    assert "ALPACA_API_KEY" in capsys.readouterr().out
