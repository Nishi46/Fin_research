import json
import math
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import pytest
from scipy.stats import ttest_1samp  # type: ignore[import-untyped]

from contracts import CapitalConfig
from harness.api import BacktestResult
from harness.thresholds import DEFAULT_THRESHOLDS
from harness.validation.holdout import (
    HOLDOUT_ENV_VAR,
    HoldoutGate,
    HoldoutLocationError,
    HoldoutNotConfiguredError,
    HoldoutVerdict,
    holdout_t_statistic,
    resolve_holdout_dir,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
START = datetime(2024, 1, 1, tzinfo=UTC)


def result(returns: Sequence[float]) -> BacktestResult:
    ts = tuple(START + timedelta(days=i) for i in range(len(returns)))
    return BacktestResult(
        capital=CapitalConfig(effective_at=START, source="default"),
        snapshot_id="holdout",
        cost_multiplier=1.0,
        seed=0,
        code_version="v",
        survivorship_biased=True,
        timestamps=ts,
        daily_returns=tuple(returns),
        trades=(),
    )


def series(mean: float, n: int = 120, seed: int = 0) -> list[float]:
    return [float(x) for x in np.random.default_rng(seed).normal(mean, 0.01, n)]


GOOD = series(0.004)  # a clear positive edge
BAD = series(-0.004)


def make_gate(tmp_path: Path, min_t_stat: float | None = None) -> HoldoutGate:
    return HoldoutGate(min_t_stat=min_t_stat, environ={HOLDOUT_ENV_VAR: str(tmp_path)})


# --- the statistic ----------------------------------------------------------------------------


@pytest.mark.parametrize("seed", range(5))
def test_t_statistic_matches_scipy(seed: int) -> None:
    returns = series(0.001, n=200, seed=seed)
    expected = float(ttest_1samp(returns, 0.0).statistic)
    assert holdout_t_statistic(returns) == pytest.approx(expected, rel=1e-9)


def test_t_statistic_scales_with_the_length_of_the_holdout() -> None:
    short = series(0.002, n=30)
    long = short * 4  # same per-day mean and volatility over a window four times as long
    ratio = holdout_t_statistic(long) / holdout_t_statistic(short)
    assert ratio == pytest.approx(2.0, rel=0.05)  # standard error shrinks with sqrt(n)


@pytest.mark.parametrize(
    "returns",
    [
        [],
        [0.01],
        [0.01] * 50,  # constant: zero standard deviation proves nothing
        [0.01, 0.02, math.nan],
        [0.01, 0.02, math.inf],
        [0.01, 0.02, -math.inf],
    ],
    ids=["empty", "one", "constant", "nan", "inf", "-inf"],
)
def test_t_statistic_fails_closed(returns: list[float]) -> None:
    assert holdout_t_statistic(returns) == 0.0


def test_a_degenerate_holdout_fails_the_gate(tmp_path: Path) -> None:
    gate = make_gate(tmp_path)
    assert not gate.evaluate("s", "constant", lambda _d: result([0.01] * 50)).passed
    assert not gate.evaluate("s", "one", lambda _d: result([0.01])).passed
    assert not gate.evaluate("s", "empty", lambda _d: result([])).passed


# --- the gate ---------------------------------------------------------------------------------


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
    good = gate.evaluate("s", "h1", lambda _dir: result(GOOD))
    bad = gate.evaluate("s", "h2", lambda _dir: result(BAD))
    assert good == HoldoutVerdict(passed=True)
    assert bad == HoldoutVerdict(passed=False)
    assert set(HoldoutVerdict.model_fields) == {"passed"}
    t = f"{holdout_t_statistic(GOOD):.4f}"[:5]
    assert t not in repr(good) + repr(bad)


def test_runner_receives_the_directory_outside_the_repo(tmp_path: Path) -> None:
    seen: list[Path] = []

    def run(directory: Path) -> BacktestResult:
        seen.append(directory)
        return result(GOOD)

    make_gate(tmp_path).evaluate("s", "h", run)
    assert seen == [tmp_path.resolve()]


def test_nothing_detailed_is_written_to_disk(tmp_path: Path) -> None:
    make_gate(tmp_path).evaluate("s", "h", lambda _dir: result(GOOD))
    text = "".join(p.read_text() for p in tmp_path.iterdir() if p.is_file())
    t = holdout_t_statistic(GOOD)
    assert f"{t:.4f}"[:5] not in text
    assert str(t)[:6] not in text
    assert [json.loads(line) for line in text.splitlines()] == [
        {"strategy_id": "s", "code_hash": "h", "passed": True}
    ]


def test_same_code_cannot_probe_the_holdout_twice(tmp_path: Path) -> None:
    first = make_gate(tmp_path).evaluate("s", "h", lambda _dir: result(GOOD))
    calls = 0

    def again(_dir: Path) -> BacktestResult:
        nonlocal calls
        calls += 1
        return result(BAD)

    second = make_gate(tmp_path).evaluate("s", "h", again)
    assert second == first
    assert calls == 0


def test_runner_errors_are_not_recorded(tmp_path: Path) -> None:
    gate = make_gate(tmp_path)

    def boom(_dir: Path) -> BacktestResult:
        raise RuntimeError("engine down")

    with pytest.raises(RuntimeError):
        gate.evaluate("s", "h", boom)
    assert gate.evaluate("s", "h", lambda _dir: result(GOOD)).passed


# --- threshold --------------------------------------------------------------------------------


def test_default_threshold_comes_from_the_configuration(tmp_path: Path) -> None:
    floor = DEFAULT_THRESHOLDS.holdout_min_t_stat
    # Find series just above and just below the configured t-statistic.
    base = series(0.0, n=100, seed=3)
    scale = np.std(base, ddof=1) / math.sqrt(len(base))
    above = [x - np.mean(base) + (floor + 0.2) * scale for x in base]
    below = [x - np.mean(base) + (floor - 0.2) * scale for x in base]
    assert holdout_t_statistic(above) > floor > holdout_t_statistic(below)
    gate = make_gate(tmp_path)
    assert gate.evaluate("s", "above", lambda _d: result(above)).passed
    assert not gate.evaluate("s", "below", lambda _d: result(below)).passed


def test_t_exactly_at_the_threshold_passes(tmp_path: Path) -> None:
    t = holdout_t_statistic(GOOD)
    assert make_gate(tmp_path, min_t_stat=t).evaluate("s", "at", lambda _d: result(GOOD)).passed
    just_above = math.nextafter(t, math.inf)
    assert (
        not make_gate(tmp_path, min_t_stat=just_above)
        .evaluate("s", "over", lambda _d: result(GOOD))
        .passed
    )


def test_explicit_threshold_overrides_the_default(tmp_path: Path) -> None:
    t = holdout_t_statistic(GOOD)
    assert t > DEFAULT_THRESHOLDS.holdout_min_t_stat
    strict = make_gate(tmp_path, min_t_stat=t + 5.0)
    assert not strict.evaluate("s", "a", lambda _d: result(GOOD)).passed
    lenient = make_gate(tmp_path, min_t_stat=0.1)
    assert lenient.evaluate("s", "b", lambda _d: result(series(0.0007, seed=2))).passed


def test_the_gate_has_no_caller_supplied_score() -> None:
    with pytest.raises(TypeError):
        HoldoutGate(score=lambda r: 1.0)  # type: ignore[call-arg]
    with pytest.raises(TypeError):
        HoldoutGate(pass_score=0.0)  # type: ignore[call-arg]
