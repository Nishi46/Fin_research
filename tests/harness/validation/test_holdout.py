import json
import math
from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import numpy as np
import pytest
from scipy.stats import ttest_1samp  # type: ignore[import-untyped]

from contracts import CapitalConfig
from harness.api import BacktestResult
from harness.data.calendar import trading_sessions
from harness.data.holdout_store import (
    MANIFEST_NAME,
    HoldoutManifestError,
    HoldoutPermissionError,
    write_holdout,
)
from harness.data.split import SplitData
from harness.data.window import Windows
from harness.thresholds import DEFAULT_THRESHOLDS
from harness.validation.holdout import (
    HOLDOUT_ENV_VAR,
    HoldoutGate,
    HoldoutLocationError,
    HoldoutNotConfiguredError,
    HoldoutQueryCapError,
    HoldoutVerdict,
    holdout_t_statistic,
    resolve_holdout_dir,
)
from tests.data_fixtures import series as bar_series

REPO_ROOT = Path(__file__).resolve().parents[3]
START = datetime(2024, 1, 1, tzinfo=UTC)
HOLDOUT_START = date(2023, 12, 1)  # before START: every default synthetic return is in-window
HOLDOUT_END = date(2025, 12, 31)


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


def make_gate(directory: Path, min_t_stat: float | None = None) -> HoldoutGate:
    return HoldoutGate(min_t_stat=min_t_stat, environ={HOLDOUT_ENV_VAR: str(directory)})


def build_holdout(directory: Path, start: date = HOLDOUT_START, end: date = HOLDOUT_END) -> Path:
    """A valid carved holdout dir (manifest + 0700) with tiny synthetic bars.

    The windows only need to be self-consistent: write_holdout does not enforce the R8 floors.
    """
    sessions = trading_sessions(start - timedelta(days=10), end)
    in_window = [d for d in sessions if d >= start][:5]
    embargo = [d for d in sessions if d < start][-3:]
    closes = [100.0 + i % 5 for i in range(len(embargo) + len(in_window))]
    bars = bar_series("AAA", closes, start=embargo[0])
    split = SplitData(
        research_bars=[],
        research_actions=[],
        embargo_bars=bars[: len(embargo)],
        embargo_actions=[],
        holdout_bars=bars[len(embargo) :],
        holdout_actions=[],
    )
    windows = Windows(
        research_end=embargo[0] - timedelta(days=1),
        holdout_start=start,
        holdout_end=end,
        embargo_sessions=len(embargo),
    )
    write_holdout(
        directory,
        split,
        windows,
        source_snapshot_id="snap",
        holdout_years=1,
        code_version="v",
    )
    return directory


@pytest.fixture
def holdout_dir(tmp_path: Path) -> Path:
    return build_holdout(tmp_path / "holdout")


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


def test_a_degenerate_holdout_fails_the_gate(holdout_dir: Path) -> None:
    gate = make_gate(holdout_dir)
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


def test_verdict_is_pass_or_fail_only(holdout_dir: Path) -> None:
    gate = make_gate(holdout_dir)
    good = gate.evaluate("s", "h1", lambda _dir: result(GOOD))
    bad = gate.evaluate("s", "h2", lambda _dir: result(BAD))
    assert good == HoldoutVerdict(passed=True)
    assert bad == HoldoutVerdict(passed=False)
    assert set(HoldoutVerdict.model_fields) == {"passed"}
    t = f"{holdout_t_statistic(GOOD):.4f}"[:5]
    assert t not in repr(good) + repr(bad)


def test_runner_receives_the_directory_outside_the_repo(holdout_dir: Path) -> None:
    seen: list[Path] = []

    def run(directory: Path) -> BacktestResult:
        seen.append(directory)
        return result(GOOD)

    make_gate(holdout_dir).evaluate("s", "h", run)
    assert seen == [holdout_dir.resolve()]


def test_nothing_detailed_is_written_to_disk(holdout_dir: Path) -> None:
    before = {p.name for p in holdout_dir.iterdir()}
    make_gate(holdout_dir).evaluate("s", "h", lambda _dir: result(GOOD))
    assert {p.name for p in holdout_dir.iterdir()} - before == {"holdout_ledger.jsonl"}
    text = (holdout_dir / "holdout_ledger.jsonl").read_text()
    t = holdout_t_statistic(GOOD)
    assert f"{t:.4f}"[:5] not in text
    assert str(t)[:6] not in text
    assert [json.loads(line) for line in text.splitlines()] == [
        {"strategy_id": "s", "code_hash": "h", "passed": True}
    ]


def test_same_code_cannot_probe_the_holdout_twice(holdout_dir: Path) -> None:
    first = make_gate(holdout_dir).evaluate("s", "h", lambda _dir: result(GOOD))
    calls = 0

    def again(_dir: Path) -> BacktestResult:
        nonlocal calls
        calls += 1
        return result(BAD)

    second = make_gate(holdout_dir).evaluate("s", "h", again)
    assert second == first
    assert calls == 0


def test_runner_errors_are_not_recorded(holdout_dir: Path) -> None:
    gate = make_gate(holdout_dir)

    def boom(_dir: Path) -> BacktestResult:
        raise RuntimeError("engine down")

    with pytest.raises(RuntimeError):
        gate.evaluate("s", "h", boom)
    assert gate.evaluate("s", "h", lambda _dir: result(GOOD)).passed


# --- lifetime query cap -----------------------------------------------------------------------


def test_default_cap_comes_from_the_configuration(holdout_dir: Path) -> None:
    cap = DEFAULT_THRESHOLDS.holdout_max_queries
    gate = make_gate(holdout_dir)
    for i in range(cap):
        gate.evaluate("s", f"h{i}", lambda _d: result(GOOD))
    with pytest.raises(HoldoutQueryCapError):
        gate.evaluate("s", "one-too-many", lambda _d: result(GOOD))


def test_the_cap_counts_across_strategies_and_gate_instances(holdout_dir: Path) -> None:
    HoldoutGate(max_queries=2, environ={HOLDOUT_ENV_VAR: str(holdout_dir)}).evaluate(
        "a", "h", lambda _d: result(GOOD)
    )
    gate = HoldoutGate(max_queries=2, environ={HOLDOUT_ENV_VAR: str(holdout_dir)})
    gate.evaluate("b", "h", lambda _d: result(BAD))
    with pytest.raises(HoldoutQueryCapError):
        gate.evaluate("c", "h", lambda _d: result(GOOD))


def test_a_refused_query_runs_nothing_and_records_nothing(holdout_dir: Path) -> None:
    gate = HoldoutGate(max_queries=1, environ={HOLDOUT_ENV_VAR: str(holdout_dir)})
    gate.evaluate("s", "h1", lambda _d: result(GOOD))
    calls = 0

    def runner(_dir: Path) -> BacktestResult:
        nonlocal calls
        calls += 1
        return result(GOOD)

    with pytest.raises(HoldoutQueryCapError):
        gate.evaluate("s", "h2", runner)
    assert calls == 0
    assert len((holdout_dir / "holdout_ledger.jsonl").read_text().splitlines()) == 1


def test_replaying_a_recorded_verdict_does_not_spend_a_query(holdout_dir: Path) -> None:
    gate = HoldoutGate(max_queries=1, environ={HOLDOUT_ENV_VAR: str(holdout_dir)})
    first = gate.evaluate("s", "h", lambda _d: result(GOOD))
    assert gate.evaluate("s", "h", lambda _d: result(BAD)) == first


def test_a_runner_error_does_not_spend_a_query(holdout_dir: Path) -> None:
    gate = HoldoutGate(max_queries=1, environ={HOLDOUT_ENV_VAR: str(holdout_dir)})

    def boom(_dir: Path) -> BacktestResult:
        raise RuntimeError("engine down")

    with pytest.raises(RuntimeError):
        gate.evaluate("s", "h", boom)
    assert gate.evaluate("s", "h", lambda _d: result(GOOD)).passed


def test_the_cap_cannot_be_set_below_one() -> None:
    with pytest.raises(ValueError, match="at least 1"):
        HoldoutGate(max_queries=0)


# --- threshold --------------------------------------------------------------------------------


def test_default_threshold_comes_from_the_configuration(holdout_dir: Path) -> None:
    floor = DEFAULT_THRESHOLDS.holdout_min_t_stat
    # Find series just above and just below the configured t-statistic.
    base = series(0.0, n=100, seed=3)
    scale = np.std(base, ddof=1) / math.sqrt(len(base))
    above = [x - np.mean(base) + (floor + 0.2) * scale for x in base]
    below = [x - np.mean(base) + (floor - 0.2) * scale for x in base]
    assert holdout_t_statistic(above) > floor > holdout_t_statistic(below)
    gate = make_gate(holdout_dir)
    assert gate.evaluate("s", "above", lambda _d: result(above)).passed
    assert not gate.evaluate("s", "below", lambda _d: result(below)).passed


def test_t_exactly_at_the_threshold_passes(holdout_dir: Path) -> None:
    t = holdout_t_statistic(GOOD)
    assert make_gate(holdout_dir, min_t_stat=t).evaluate("s", "at", lambda _d: result(GOOD)).passed
    just_above = math.nextafter(t, math.inf)
    assert (
        not make_gate(holdout_dir, min_t_stat=just_above)
        .evaluate("s", "over", lambda _d: result(GOOD))
        .passed
    )


def test_explicit_threshold_overrides_the_default(holdout_dir: Path) -> None:
    t = holdout_t_statistic(GOOD)
    assert t > DEFAULT_THRESHOLDS.holdout_min_t_stat
    strict = make_gate(holdout_dir, min_t_stat=t + 5.0)
    assert not strict.evaluate("s", "a", lambda _d: result(GOOD)).passed
    lenient = make_gate(holdout_dir, min_t_stat=0.1)
    assert lenient.evaluate("s", "b", lambda _d: result(series(0.0007, seed=2))).passed


def test_the_gate_has_no_caller_supplied_score() -> None:
    with pytest.raises(TypeError):
        HoldoutGate(score=lambda r: 1.0)  # type: ignore[call-arg]
    with pytest.raises(TypeError):
        HoldoutGate(pass_score=0.0)  # type: ignore[call-arg]


# --- manifest-defined verdict window ---------------------------------------------------------


def _ledger_lines(directory: Path) -> list[str]:
    ledger = directory / "holdout_ledger.jsonl"
    return ledger.read_text().splitlines() if ledger.exists() else []


class _Runner:
    def __init__(self, res: BacktestResult) -> None:
        self.res = res
        self.calls = 0

    def __call__(self, _dir: Path) -> BacktestResult:
        self.calls += 1
        return self.res


def test_a_directory_without_a_manifest_is_refused(tmp_path: Path) -> None:
    bare = tmp_path / "bare"
    bare.mkdir(mode=0o700)
    runner = _Runner(result(GOOD))
    with pytest.raises(HoldoutManifestError):
        make_gate(bare).evaluate("s", "h", runner)
    assert runner.calls == 0
    assert _ledger_lines(bare) == []


def test_a_tampered_manifest_date_is_refused(holdout_dir: Path) -> None:
    path = holdout_dir / MANIFEST_NAME
    path.write_text(path.read_text().replace(HOLDOUT_START.isoformat(), "2023-01-03"))
    runner = _Runner(result(GOOD))
    with pytest.raises(HoldoutManifestError):
        make_gate(holdout_dir).evaluate("s", "h", runner)
    assert runner.calls == 0
    assert _ledger_lines(holdout_dir) == []


def test_a_tampered_data_file_is_refused(holdout_dir: Path) -> None:
    target = next((holdout_dir / "bars").glob("*.parquet"))
    target.write_bytes(target.read_bytes() + b"x")
    runner = _Runner(result(GOOD))
    with pytest.raises(HoldoutManifestError):
        make_gate(holdout_dir).evaluate("s", "h", runner)
    assert runner.calls == 0
    assert _ledger_lines(holdout_dir) == []


def test_a_loose_directory_mode_is_refused(holdout_dir: Path) -> None:
    holdout_dir.chmod(0o755)
    runner = _Runner(result(GOOD))
    with pytest.raises(HoldoutPermissionError):
        make_gate(holdout_dir).evaluate("s", "h", runner)
    assert runner.calls == 0
    assert _ledger_lines(holdout_dir) == []


def _result_at(returns: Sequence[float], dates: Sequence[date]) -> BacktestResult:
    base = result(returns)
    stamps = tuple(datetime(d.year, d.month, d.day, 21, tzinfo=UTC) for d in dates)
    return base.model_copy(update={"timestamps": stamps})


def _consecutive(start: date, n: int) -> list[date]:
    return [start + timedelta(days=i) for i in range(n)]


NOISE = series(0.0, n=100, seed=7)


def test_edge_only_before_holdout_start_does_not_move_the_verdict(tmp_path: Path) -> None:
    assert holdout_t_statistic(NOISE) < DEFAULT_THRESHOLDS.holdout_min_t_stat
    directory = build_holdout(tmp_path / "h", start=date(2024, 3, 1), end=date(2024, 12, 31))
    returns = series(0.02, n=100, seed=1) + NOISE  # planted edge in warm-up/embargo
    dates = _consecutive(date(2023, 11, 1), 100) + _consecutive(date(2024, 3, 1), 100)
    verdict = make_gate(directory).evaluate("s", "h", lambda _d: _result_at(returns, dates))
    assert not verdict.passed


def test_edge_only_inside_the_window_passes(tmp_path: Path) -> None:
    directory = build_holdout(tmp_path / "h", start=date(2024, 3, 1), end=date(2024, 12, 31))
    returns = NOISE + series(0.004, n=120, seed=1)
    dates = _consecutive(date(2023, 11, 1), 100) + _consecutive(date(2024, 3, 1), 120)
    verdict = make_gate(directory).evaluate("s", "h", lambda _d: _result_at(returns, dates))
    assert verdict.passed


def test_returns_after_holdout_end_are_ignored(tmp_path: Path) -> None:
    directory = build_holdout(tmp_path / "h", start=date(2024, 3, 1), end=date(2024, 6, 30))
    returns = NOISE + series(0.02, n=100, seed=1)
    dates = _consecutive(date(2024, 3, 1), 100) + _consecutive(date(2024, 7, 1), 100)
    verdict = make_gate(directory).evaluate("s", "h", lambda _d: _result_at(returns, dates))
    assert not verdict.passed


def test_the_boundary_dates_are_inclusive_in_ny_session_time(tmp_path: Path) -> None:
    directory = build_holdout(tmp_path / "h", start=date(2024, 3, 1), end=date(2024, 3, 3))
    returns = [5.0, 0.01, 0.02, 0.03, 9.0]  # only the middle three are in [start, end]
    dates = [
        date(2024, 2, 29),
        date(2024, 3, 1),
        date(2024, 3, 2),
        date(2024, 3, 3),
        date(2024, 3, 4),
    ]
    res = _result_at(returns, dates)
    gate = make_gate(directory, min_t_stat=holdout_t_statistic([0.01, 0.02, 0.03]))
    assert gate.evaluate("s", "h", lambda _d: res).passed


def test_a_length_mismatch_fails_closed_and_records_nothing(holdout_dir: Path) -> None:
    bad = result(GOOD).model_copy(update={"timestamps": result(GOOD).timestamps[:-1]})
    with pytest.raises(ValueError, match="timestamps"):
        make_gate(holdout_dir).evaluate("s", "h", lambda _d: bad)
    assert _ledger_lines(holdout_dir) == []
