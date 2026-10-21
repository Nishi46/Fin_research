"""Hidden final holdout.

The holdout data lives outside the repository, in the directory named by the environment
variable `FIN_RESEARCH_HOLDOUT_DIR`. There is no default path and no committed path: if the
variable is unset, empty, missing on disk, or points inside the repository, the gate refuses.

What the proposer side can learn is a single boolean. The harness computes the statistic
itself: the t-statistic of the net daily returns of the holdout backtest (mean divided by its
standard error), and the gate passes iff t >= `min_t_stat` (default
`DEFAULT_THRESHOLDS.holdout_min_t_stat`). Nothing the caller supplies can make the bar more
lenient. A t-statistic scales with the holdout's length, where a fixed Sharpe is a weaker bar on
a short window. No minimum holdout length is enforced beyond two returns: the statistic already
accounts for length, and how long the holdout should be is the owner's open decision. The number
never leaves `evaluate`, and nothing but the boolean is written to the ledger.

Repeated queries are a leak (a proposer could bisect on a score), so a verdict is recorded per
`(strategy_id, code_hash)` and a second query for the same code returns the recorded verdict
without running anything. New code is a new trial and gets its own single query.

Each new code version still leaks one bit, so the ledger also bounds the total: once
`holdout_max_queries` verdicts are recorded (across all strategies), the gate refuses new code
until the owner raises the cap. Replays and runner errors do not spend a query.

The verdict window is defined by the directory's manifest, never by the caller. Before anything
is read or run, `verify_holdout_dir` checks the directory (mode 0700, manifest present, content
hash matches); any `HoldoutStoreError` propagates, so a directory without a valid manifest is
refused and nothing is scored or recorded. The backtest also covers the embargo and warm-up
bars that lie before `holdout_start`; only returns whose New York session date falls in
[`holdout_start`, `holdout_end`] are scored.

The `run` callable receives the holdout path, so it must be constructed by the harness (the
pipeline) and never supplied by the proposer. Software cannot enforce that alone: OS-level
isolation of the holdout from the proposer is the plan before build step 5.
"""

import fcntl
import json
import math
import os
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np
from pydantic import BaseModel, ConfigDict

from harness.api import BacktestResult
from harness.data.holdout_store import HoldoutManifestError, verify_holdout_dir
from harness.thresholds import DEFAULT_THRESHOLDS

HOLDOUT_ENV_VAR = "FIN_RESEARCH_HOLDOUT_DIR"
LEDGER_NAME = "holdout_ledger.jsonl"
LOCK_NAME = "holdout_ledger.lock"
_FILE_MODE = 0o600
MIN_RETURNS = 2
_NY = ZoneInfo("America/New_York")
_REPO_ROOT = Path(__file__).resolve().parents[2]


class HoldoutNotConfiguredError(Exception):
    """The holdout directory is not set, or does not exist."""


class HoldoutLocationError(Exception):
    """The holdout directory is inside the repository."""


class HoldoutQueryCapError(Exception):
    """The lifetime cap on holdout queries is used up."""


class HoldoutLedgerError(Exception):
    """The ledger cannot be read or trusted; the gate refuses rather than guess."""


class HoldoutVerdict(BaseModel):
    """The only thing the proposer-facing side ever sees."""

    model_config = ConfigDict(frozen=True)

    passed: bool


def resolve_holdout_dir(environ: Mapping[str, str] | None = None) -> Path:
    env = os.environ if environ is None else environ
    raw = env.get(HOLDOUT_ENV_VAR, "").strip()
    if not raw:
        raise HoldoutNotConfiguredError(f"{HOLDOUT_ENV_VAR} is not set")
    path = Path(raw).expanduser().resolve()
    if path == _REPO_ROOT or _REPO_ROOT in path.parents:
        raise HoldoutLocationError("the holdout must be stored outside the repository")
    if not path.is_dir():
        raise HoldoutNotConfiguredError(f"holdout directory {path} does not exist")
    return path


def holdout_t_statistic(returns: Sequence[float]) -> float:
    """t = mean / (sample std with ddof=1 / sqrt(n)). Fails closed to 0.0.

    Returns 0.0 for fewer than two returns, any non-finite return, or a zero or non-finite
    standard deviation: constant returns cannot prove an edge.
    """
    values = np.asarray(returns, dtype=np.float64)
    if values.size < MIN_RETURNS or not np.all(np.isfinite(values)):
        return 0.0
    std = float(np.std(values, ddof=1))
    if not math.isfinite(std) or std <= 0.0:
        return 0.0
    return float(np.mean(values)) / (std / math.sqrt(values.size))


class HoldoutGate:
    def __init__(
        self,
        *,
        min_t_stat: float | None = None,
        max_queries: int | None = None,
        environ: Mapping[str, str] | None = None,
    ) -> None:
        floor = DEFAULT_THRESHOLDS.holdout_min_t_stat
        cap = DEFAULT_THRESHOLDS.holdout_max_queries
        t_stat = floor if min_t_stat is None else min_t_stat
        if (
            isinstance(t_stat, bool)
            or not isinstance(t_stat, int | float)
            or not math.isfinite(t_stat)
            or t_stat < floor
        ):
            raise ValueError(f"min_t_stat must be a finite number >= the default {floor}")
        queries = cap if max_queries is None else max_queries
        if type(queries) is not int or not 1 <= queries <= cap:
            raise ValueError(f"max_queries must be an int, at least 1 and at most {cap}")
        self._min_t_stat = float(t_stat)
        self._max_queries = queries
        self._environ = environ

    def evaluate(
        self,
        strategy_id: str,
        code_hash: str,
        run: Callable[[Path], BacktestResult],
    ) -> HoldoutVerdict:
        """Run `run(holdout_dir)` once per code version and return pass or fail."""
        directory = resolve_holdout_dir(self._environ)
        manifest = verify_holdout_dir(directory)
        ledger = directory / LEDGER_NAME
        # The lock spans lookup, cap check, run and append, so two processes can neither both
        # pass the cap nor both run one key.
        with _exclusive_lock(directory / LOCK_NAME):
            entries = _read_ledger(ledger)
            for entry in entries:
                if entry["strategy_id"] == strategy_id and entry["code_hash"] == code_hash:
                    return HoldoutVerdict(passed=entry["passed"])
            if len(entries) >= self._max_queries:
                raise HoldoutQueryCapError(
                    f"the holdout allows {self._max_queries} queries in total and all are used"
                )

            backtest = run(directory)
            if len(backtest.timestamps) != len(backtest.daily_returns):
                raise ValueError("timestamps and daily_returns must have the same length")
            scored = [
                r
                for ts, r in zip(backtest.timestamps, backtest.daily_returns, strict=True)
                if manifest.holdout_start <= ts.astimezone(_NY).date() <= manifest.holdout_end
            ]
            passed = holdout_t_statistic(scored) >= self._min_t_stat
            # The runner had the directory in hand: nothing is recorded unless it is unchanged.
            if verify_holdout_dir(directory) != manifest:
                raise HoldoutManifestError("holdout manifest changed while the runner executed")
            _append_ledger(
                ledger, {"strategy_id": strategy_id, "code_hash": code_hash, "passed": passed}
            )
        return HoldoutVerdict(passed=passed)


@contextmanager
def _exclusive_lock(path: Path) -> Iterator[None]:
    fd = os.open(path, os.O_RDWR | os.O_CREAT, _FILE_MODE)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)  # closing releases the flock


def _read_ledger(ledger: Path) -> list[dict[str, Any]]:
    if not ledger.exists():
        return []
    try:
        text = ledger.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        raise HoldoutLedgerError(f"holdout ledger unreadable: {e}") from e
    entries: list[dict[str, Any]] = []
    for number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except ValueError as e:
            raise HoldoutLedgerError(f"holdout ledger line {number} is not valid JSON") from e
        if (
            not isinstance(entry, dict)
            or not isinstance(entry.get("strategy_id"), str)
            or not isinstance(entry.get("code_hash"), str)
        ):
            raise HoldoutLedgerError(f"holdout ledger line {number} is malformed")
        if not isinstance(entry.get("passed"), bool):
            raise HoldoutLedgerError(f"holdout ledger line {number}: 'passed' must be a boolean")
        entries.append(entry)
    return entries


def _append_ledger(ledger: Path, entry: Mapping[str, object]) -> None:
    fd = os.open(ledger, os.O_RDWR | os.O_CREAT | os.O_APPEND, _FILE_MODE)
    try:
        size = os.fstat(fd).st_size
        # A torn earlier write must not fuse with this entry.
        prefix = b"\n" if size and os.pread(fd, 1, size - 1) != b"\n" else b""
        os.write(fd, prefix + json.dumps(entry).encode("utf-8") + b"\n")
        os.fsync(fd)
    finally:
        os.close(fd)
