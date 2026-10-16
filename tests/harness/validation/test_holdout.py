import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from contracts import CapitalConfig
from harness.api import BacktestResult
from harness.validation.holdout import (
    HOLDOUT_ENV_VAR,
    HoldoutGate,
    HoldoutLocationError,
    HoldoutNotConfiguredError,
    HoldoutVerdict,
    resolve_holdout_dir,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
START = datetime(2024, 1, 1, tzinfo=UTC)
SECRET_SCORE = 0.8123456789


def result(daily: float) -> BacktestResult:
    ts = tuple(START + timedelta(days=i) for i in range(5))
    return BacktestResult(
        capital=CapitalConfig(effective_at=START, source="default"),
        snapshot_id="holdout",
        cost_multiplier=1.0,
        seed=0,
        code_version="v",
        survivorship_biased=True,
        timestamps=ts,
        daily_returns=tuple(daily for _ in ts),
        trades=(),
    )


def total_return(r: BacktestResult) -> float:
    return sum(r.daily_returns)


def make_gate(tmp_path: Path, pass_score: float = 0.0) -> HoldoutGate:
    return HoldoutGate(
        score=lambda r: SECRET_SCORE * total_return(r) / max(abs(total_return(r)), 1e-12),
        pass_score=pass_score,
        environ={HOLDOUT_ENV_VAR: str(tmp_path)},
    )


def test_path_comes_from_the_environment(tmp_path: Path) -> None:
    assert resolve_holdout_dir({HOLDOUT_ENV_VAR: str(tmp_path)}) == tmp_path.resolve()


def test_missing_variable_is_an_error_not_a_default() -> None:
    with pytest.raises(HoldoutNotConfiguredError):
        resolve_holdout_dir({})
    with pytest.raises(HoldoutNotConfiguredError):
        resolve_holdout_dir({HOLDOUT_ENV_VAR: "  "})


@pytest.mark.parametrize("inside", ["", "data", "../Fin_research/data"])
def test_a_path_inside_the_repository_is_refused(inside: str) -> None:
    with pytest.raises(HoldoutLocationError):
        resolve_holdout_dir({HOLDOUT_ENV_VAR: str(REPO_ROOT / inside)})


def test_a_missing_directory_is_refused(tmp_path: Path) -> None:
    with pytest.raises(HoldoutNotConfiguredError):
        resolve_holdout_dir({HOLDOUT_ENV_VAR: str(tmp_path / "nope")})


def test_verdict_is_pass_or_fail_only(tmp_path: Path) -> None:
    gate = make_gate(tmp_path)
    good = gate.evaluate("s", "h1", lambda _dir: result(0.01))
    bad = gate.evaluate("s", "h2", lambda _dir: result(-0.01))
    assert good == HoldoutVerdict(passed=True)
    assert bad == HoldoutVerdict(passed=False)
    assert set(HoldoutVerdict.model_fields) == {"passed"}
    assert "0.81" not in repr(good) + repr(bad)


def test_runner_receives_the_directory_outside_the_repo(tmp_path: Path) -> None:
    seen: list[Path] = []

    def run(directory: Path) -> BacktestResult:
        seen.append(directory)
        return result(0.01)

    make_gate(tmp_path).evaluate("s", "h", run)
    assert seen == [tmp_path.resolve()]


def test_nothing_detailed_is_written_to_disk(tmp_path: Path) -> None:
    gate = make_gate(tmp_path)
    gate.evaluate("s", "h", lambda _dir: result(0.01))
    text = "".join(p.read_text() for p in tmp_path.iterdir() if p.is_file())
    assert str(SECRET_SCORE)[:6] not in text
    assert [json.loads(line) for line in text.splitlines()] == [
        {"strategy_id": "s", "code_hash": "h", "passed": True}
    ]


def test_same_code_cannot_probe_the_holdout_twice(tmp_path: Path) -> None:
    gate = make_gate(tmp_path)
    first = gate.evaluate("s", "h", lambda _dir: result(0.01))
    calls = 0

    def again(_dir: Path) -> BacktestResult:
        nonlocal calls
        calls += 1
        return result(-0.01)

    second = HoldoutGate(
        score=lambda r: total_return(r),
        pass_score=0.0,
        environ={HOLDOUT_ENV_VAR: str(tmp_path)},
    ).evaluate("s", "h", again)
    assert second == first
    assert calls == 0


def test_runner_errors_are_not_recorded(tmp_path: Path) -> None:
    gate = make_gate(tmp_path)

    def boom(_dir: Path) -> BacktestResult:
        raise RuntimeError("engine down")

    with pytest.raises(RuntimeError):
        gate.evaluate("s", "h", boom)
    assert gate.evaluate("s", "h", lambda _dir: result(0.01)).passed


def fixed_score_gate(tmp_path: Path, score: float, **kwargs: float) -> HoldoutGate:
    return HoldoutGate(score=lambda _r: score, environ={HOLDOUT_ENV_VAR: str(tmp_path)}, **kwargs)


def test_default_pass_score_is_the_configured_holdout_sharpe(tmp_path: Path) -> None:
    from harness.thresholds import DEFAULT_THRESHOLDS

    floor = DEFAULT_THRESHOLDS.holdout_min_sharpe
    above = fixed_score_gate(tmp_path, floor).evaluate("s", "a", lambda _d: result(0.01))
    below = fixed_score_gate(tmp_path, floor - 0.01).evaluate("s", "b", lambda _d: result(0.01))
    assert above.passed
    assert not below.passed
    assert set(HoldoutVerdict.model_fields) == {"passed"}


def test_explicit_pass_score_overrides_the_default(tmp_path: Path) -> None:
    gate = fixed_score_gate(tmp_path, 0.2, pass_score=0.1)
    assert gate.evaluate("s", "a", lambda _d: result(0.01)).passed
    strict = fixed_score_gate(tmp_path, 0.9, pass_score=1.0)
    assert not strict.evaluate("s", "b", lambda _d: result(0.01)).passed
