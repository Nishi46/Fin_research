"""Hidden final holdout.

The holdout data lives outside the repository, in the directory named by the environment
variable `FIN_RESEARCH_HOLDOUT_DIR`. There is no default path and no committed path: if the
variable is unset, empty, missing on disk, or points inside the repository, the gate refuses.

What the proposer side can learn is a single boolean. The score function and pass score are
supplied by the caller, and the pass score defaults to `DEFAULT_THRESHOLDS.holdout_min_sharpe`
(nothing is hard-coded here). The numeric score never leaves `evaluate`, and nothing but the
boolean is written to the ledger.

Repeated queries are a leak (a proposer could bisect on a score), so a verdict is recorded per
`(strategy_id, code_hash)` and a second query for the same code returns the recorded verdict
without running anything. New code is a new trial and gets its own single query.
"""

import json
import os
from collections.abc import Callable, Mapping
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from harness.api import BacktestResult
from harness.thresholds import DEFAULT_THRESHOLDS

HOLDOUT_ENV_VAR = "FIN_RESEARCH_HOLDOUT_DIR"
LEDGER_NAME = "holdout_ledger.jsonl"
_REPO_ROOT = Path(__file__).resolve().parents[2]


class HoldoutNotConfiguredError(Exception):
    """The holdout directory is not set, or does not exist."""


class HoldoutLocationError(Exception):
    """The holdout directory is inside the repository."""


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


class HoldoutGate:
    def __init__(
        self,
        *,
        score: Callable[[BacktestResult], float],
        pass_score: float | None = None,
        environ: Mapping[str, str] | None = None,
    ) -> None:
        self._score = score
        self._pass_score = (
            DEFAULT_THRESHOLDS.holdout_min_sharpe if pass_score is None else pass_score
        )
        self._environ = environ

    def evaluate(
        self,
        strategy_id: str,
        code_hash: str,
        run: Callable[[Path], BacktestResult],
    ) -> HoldoutVerdict:
        """Run `run(holdout_dir)` once per code version and return pass or fail."""
        directory = resolve_holdout_dir(self._environ)
        ledger = directory / LEDGER_NAME
        recorded = self._lookup(ledger, strategy_id, code_hash)
        if recorded is not None:
            return HoldoutVerdict(passed=recorded)

        passed = self._score(run(directory)) >= self._pass_score
        entry = {"strategy_id": strategy_id, "code_hash": code_hash, "passed": passed}
        with ledger.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry) + "\n")
        return HoldoutVerdict(passed=passed)

    @staticmethod
    def _lookup(ledger: Path, strategy_id: str, code_hash: str) -> bool | None:
        if not ledger.exists():
            return None
        for line in ledger.read_text(encoding="utf-8").splitlines():
            entry = json.loads(line)
            if entry["strategy_id"] == strategy_id and entry["code_hash"] == code_hash:
                return bool(entry["passed"])
        return None
