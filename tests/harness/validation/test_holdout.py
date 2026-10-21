import json
import math
import threading
import time
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
    HoldoutLedgerError,
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
    assert {p.name for p in holdout_dir.iterdir()} - before == {
        "holdout_ledger.jsonl",
        "holdout_ledger.lock",
    }
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


@pytest.mark.parametrize(
    "bad",
    [0.0, 0.5, DEFAULT_THRESHOLDS.holdout_min_t_stat - 1e-9, -1.0, math.nan, -math.inf, math.inf],
)
def test_min_t_stat_cannot_be_more_lenient_than_the_default(bad: float) -> None:
    with pytest.raises(ValueError, match="min_t_stat"):
        HoldoutGate(min_t_stat=bad)


def test_min_t_stat_at_the_default_is_accepted() -> None:
    HoldoutGate(min_t_stat=DEFAULT_THRESHOLDS.holdout_min_t_stat)
    HoldoutGate(min_t_stat=DEFAULT_THRESHOLDS.holdout_min_t_stat + 3.0)


def test_a_zero_threshold_cannot_pass_constant_zero_returns(holdout_dir: Path) -> None:
    with pytest.raises(ValueError, match="min_t_stat"):
        make_gate(holdout_dir, min_t_stat=0.0)


@pytest.mark.parametrize(
    "bad", [0, -1, DEFAULT_THRESHOLDS.holdout_max_queries + 1, 10**9, 1.5, True, "5", math.nan]
)
def test_max_queries_must_be_an_int_within_the_default_cap(bad: object) -> None:
    with pytest.raises(ValueError, match="max_queries"):
        HoldoutGate(max_queries=bad)  # type: ignore[arg-type]


def test_max_queries_at_the_bounds_is_accepted() -> None:
    HoldoutGate(max_queries=1)
    HoldoutGate(max_queries=DEFAULT_THRESHOLDS.holdout_max_queries)


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


def test_explicit_threshold_can_be_stricter_than_the_default(holdout_dir: Path) -> None:
    t = holdout_t_statistic(GOOD)
    assert t > DEFAULT_THRESHOLDS.holdout_min_t_stat
    strict = make_gate(holdout_dir, min_t_stat=t + 5.0)
    assert not strict.evaluate("s", "a", lambda _d: result(GOOD)).passed


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


# --- review fixes: strict replay, ledger robustness, locking, TOCTOU, NY session filter --------


def _ledger(directory: Path) -> Path:
    return directory / "holdout_ledger.jsonl"


@pytest.mark.parametrize("bad", ["false", "true", 0, 1, None, "False"])
def test_a_non_boolean_recorded_verdict_fails_closed(holdout_dir: Path, bad: object) -> None:
    _ledger(holdout_dir).write_text(
        json.dumps({"strategy_id": "s", "code_hash": "h", "passed": bad}) + "\n"
    )
    runner = _Runner(result(GOOD))
    with pytest.raises(HoldoutLedgerError, match="passed"):
        make_gate(holdout_dir).evaluate("s", "h", runner)
    assert runner.calls == 0


def test_blank_lines_are_neither_queries_nor_parse_errors(holdout_dir: Path) -> None:
    entry = json.dumps({"strategy_id": "a", "code_hash": "h", "passed": True})
    _ledger(holdout_dir).write_text("\n\n" + entry + "\n\n   \n")
    gate = HoldoutGate(max_queries=2, environ={HOLDOUT_ENV_VAR: str(holdout_dir)})
    assert gate.evaluate("a", "h", lambda _d: result(BAD)).passed  # replayed
    assert gate.evaluate("b", "h", lambda _d: result(GOOD)).passed  # 1 used + blanks, not 4
    with pytest.raises(HoldoutQueryCapError):
        gate.evaluate("c", "h", lambda _d: result(GOOD))


def test_a_torn_ledger_write_does_not_fuse_the_next_entry(holdout_dir: Path) -> None:
    first = json.dumps({"strategy_id": "a", "code_hash": "h", "passed": True})
    _ledger(holdout_dir).write_text(first)  # no trailing newline
    make_gate(holdout_dir).evaluate("b", "h", lambda _d: result(GOOD))
    lines = _ledger(holdout_dir).read_text().splitlines()
    assert [json.loads(line)["strategy_id"] for line in lines] == ["a", "b"]


def test_a_malformed_ledger_line_names_its_line_number(holdout_dir: Path) -> None:
    good = json.dumps({"strategy_id": "a", "code_hash": "h", "passed": True})
    _ledger(holdout_dir).write_text(good + "\n{not json\n")
    runner = _Runner(result(GOOD))
    with pytest.raises(HoldoutLedgerError, match="line 2"):
        make_gate(holdout_dir).evaluate("b", "h", runner)
    assert runner.calls == 0


def test_a_ledger_entry_missing_fields_is_a_ledger_error(holdout_dir: Path) -> None:
    _ledger(holdout_dir).write_text('{"strategy_id": "a"}\n')
    with pytest.raises(HoldoutLedgerError, match="line 1"):
        make_gate(holdout_dir).evaluate("b", "h", _Runner(result(GOOD)))


def test_the_lock_file_is_private_and_does_not_break_verification(holdout_dir: Path) -> None:
    make_gate(holdout_dir).evaluate("s", "h", lambda _d: result(GOOD))
    lock = holdout_dir / "holdout_ledger.lock"
    assert lock.exists()
    assert (lock.stat().st_mode & 0o777) == 0o600
    # a second query still verifies the directory with the lock file present
    assert make_gate(holdout_dir).evaluate("s", "h2", lambda _d: result(GOOD)).passed


def test_concurrent_queries_for_one_key_run_the_runner_once(holdout_dir: Path) -> None:
    calls = 0
    verdicts: list[bool] = []

    def slow(_d: Path) -> BacktestResult:
        nonlocal calls
        calls += 1
        time.sleep(0.3)
        return result(GOOD)

    def go() -> None:
        verdicts.append(make_gate(holdout_dir).evaluate("s", "h", slow).passed)

    threads = [threading.Thread(target=go) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert calls == 1
    assert verdicts == [True, True]
    assert len(_ledger_lines(holdout_dir)) == 1


def test_concurrent_queries_cannot_both_pass_the_cap(holdout_dir: Path) -> None:
    outcomes: list[str] = []

    def slow(_d: Path) -> BacktestResult:
        time.sleep(0.3)
        return result(GOOD)

    def go(name: str) -> None:
        gate = HoldoutGate(max_queries=1, environ={HOLDOUT_ENV_VAR: str(holdout_dir)})
        try:
            gate.evaluate(name, "h", slow)
            outcomes.append("ok")
        except HoldoutQueryCapError:
            outcomes.append("capped")

    threads = [threading.Thread(target=go, args=(n,)) for n in ("a", "b")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(outcomes) == ["capped", "ok"]
    assert len(_ledger_lines(holdout_dir)) == 1


def test_data_tampered_while_the_runner_executes_records_nothing(holdout_dir: Path) -> None:
    def tamper(_d: Path) -> BacktestResult:
        target = next((holdout_dir / "bars").glob("*.parquet"))
        target.write_bytes(target.read_bytes() + b"x")
        return result(GOOD)

    with pytest.raises(HoldoutManifestError):
        make_gate(holdout_dir).evaluate("s", "h", tamper)
    assert _ledger_lines(holdout_dir) == []


def test_manifest_replaced_with_a_valid_one_while_running_records_nothing(
    holdout_dir: Path,
) -> None:
    from harness.data import holdout_store

    def swap(_d: Path) -> BacktestResult:
        manifest = holdout_store.load_manifest(holdout_dir)
        new = manifest.model_copy(update={"source_snapshot_id": "other"})
        new = new.model_copy(update={"content_sha256": holdout_store._hash_for(holdout_dir, new)})
        (holdout_dir / MANIFEST_NAME).write_text(new.model_dump_json(indent=2))
        holdout_store.verify_holdout_dir(holdout_dir)  # the new manifest is self-consistent
        return result(GOOD)

    with pytest.raises(HoldoutManifestError, match="changed"):
        make_gate(holdout_dir).evaluate("s", "h", swap)
    assert _ledger_lines(holdout_dir) == []


# NY session date vs UTC date. EST is UTC-5 and EDT is UTC-4, so a stamp between 00:00 and
# 04:00/05:00 UTC belongs to the previous New York session.


def _stamped(returns: Sequence[float], stamps: Sequence[datetime]) -> BacktestResult:
    return result(returns).model_copy(update={"timestamps": tuple(stamps)})


def _z(y: int, m: int, d: int, h: int) -> datetime:
    return datetime(y, m, d, h, tzinfo=UTC)


def _passes_iff_exactly_these_count(
    tmp_path: Path,
    start: date,
    end: date,
    counted: Sequence[datetime],
    excluded: Sequence[datetime],
) -> None:
    """Outliers sit at the excluded stamps; the bar is the t-stat of the counted returns."""
    directory = build_holdout(tmp_path / "h", start=start, end=end)
    in_returns = [0.01, 0.02, 0.03][: len(counted)] if len(counted) <= 3 else [0.02] * len(counted)
    out_returns = [5.0, 9.0][: len(excluded)]
    bar = holdout_t_statistic(in_returns)
    assert bar >= DEFAULT_THRESHOLDS.holdout_min_t_stat
    assert holdout_t_statistic([*in_returns, *out_returns]) < bar  # sanity: leakage would fail
    res = _stamped([*in_returns, *out_returns], [*counted, *excluded])
    assert make_gate(directory, min_t_stat=bar).evaluate("s", "h", lambda _d: res).passed


@pytest.mark.parametrize("hour", [1, 3, 4])
def test_utc_early_morning_stamps_belong_to_the_previous_ny_session_winter(
    tmp_path: Path, hour: int
) -> None:
    # Window [2024-03-01, 2024-03-03], all EST. UTC Mar 1 hh:00 is NY Feb 29 evening: excluded
    # although its UTC date is inside. UTC Mar 4 hh:00 is NY Mar 3 evening: included although its
    # UTC date is past the end.
    _passes_iff_exactly_these_count(
        tmp_path,
        date(2024, 3, 1),
        date(2024, 3, 3),
        counted=[_z(2024, 3, 2, 21), _z(2024, 3, 3, 21), _z(2024, 3, 4, hour)],
        excluded=[_z(2024, 3, 1, hour)],
    )


@pytest.mark.parametrize("hour", [1, 3])
def test_utc_early_morning_stamps_belong_to_the_previous_ny_session_summer(
    tmp_path: Path, hour: int
) -> None:
    # EDT (UTC-4): UTC Jun 3 01:00/03:00 is NY Jun 2 evening; UTC Jun 6 hh:00 is NY Jun 5 evening.
    _passes_iff_exactly_these_count(
        tmp_path,
        date(2024, 6, 3),
        date(2024, 6, 5),
        counted=[_z(2024, 6, 3, 20), _z(2024, 6, 4, 20), _z(2024, 6, 6, hour)],
        excluded=[_z(2024, 6, 3, hour)],
    )


def test_a_utc_stamp_after_midnight_in_new_york_summer_is_the_next_session(
    tmp_path: Path,
) -> None:
    # 04:00 UTC in EDT is 00:00 New York: already the new date, so on the start boundary it counts.
    _passes_iff_exactly_these_count(
        tmp_path,
        date(2024, 6, 3),
        date(2024, 6, 5),
        counted=[_z(2024, 6, 3, 4), _z(2024, 6, 4, 20), _z(2024, 6, 5, 20)],
        excluded=[_z(2024, 6, 2, 20)],
    )


def test_the_winter_close_at_2100_utc_is_the_same_ny_session(tmp_path: Path) -> None:
    # 21:00 UTC = 16:00 EST. End boundary day counts, the next day's close does not.
    _passes_iff_exactly_these_count(
        tmp_path,
        date(2024, 3, 1),
        date(2024, 3, 4),
        counted=[_z(2024, 3, 1, 21), _z(2024, 3, 3, 21), _z(2024, 3, 4, 21)],
        excluded=[_z(2024, 3, 5, 21)],
    )


def test_a_half_day_close_at_1800_utc_is_the_same_ny_session(tmp_path: Path) -> None:
    # 2024-11-29 early close, 13:00 EST = 18:00 UTC, on the start boundary.
    _passes_iff_exactly_these_count(
        tmp_path,
        date(2024, 11, 29),
        date(2024, 12, 2),
        counted=[_z(2024, 11, 29, 18), _z(2024, 12, 2, 21), _z(2024, 12, 2, 18)],
        excluded=[_z(2024, 11, 28, 21)],
    )
