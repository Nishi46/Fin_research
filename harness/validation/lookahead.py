"""Lookahead check: signals at time t must not change when data after t is removed.

For each check time t the strategy runs twice: once on the full provider and once on a provider
that physically cannot return anything after t. Any difference means the signal used the
future. A strategy that makes the view raise `LookaheadError` also fails.

This catches a strategy that reaches around `DataView` to the provider, or that normalizes with
full-history statistics. It cannot catch a leak that is also present in the truncated data
(for example prices already adjusted using future corporate actions); that is a data-layer test.
"""

import math
from collections.abc import Sequence
from datetime import UTC, date, datetime

from contracts import Intent, LookaheadError, Stage, Strategy
from contracts._time import ensure_utc
from contracts.data import Bar, CorporateAction, DataProvider, Snapshot
from contracts.dataview import DataView
from harness.api import StageResult

DEFAULT_WEIGHT_TOLERANCE = 1e-12
FAR_FUTURE = datetime(9999, 12, 31, tzinfo=UTC)


class TruncatedProvider:
    """A provider that behaves as if the world ended at `cutoff`."""

    def __init__(self, inner: DataProvider, cutoff: datetime) -> None:
        self._inner = inner
        self._cutoff = ensure_utc(cutoff)

    def snapshot(self) -> Snapshot:
        return self._inner.snapshot()

    def bars(self, symbols: Sequence[str], end: datetime) -> Sequence[Bar]:
        limit = min(ensure_utc(end), self._cutoff)
        return [b for b in self._inner.bars(symbols, limit) if b.ts <= self._cutoff]

    def corporate_actions(self, symbols: Sequence[str], end: datetime) -> Sequence[CorporateAction]:
        limit = min(ensure_utc(end), self._cutoff)
        return [
            a
            for a in self._inner.corporate_actions(symbols, limit)
            if a.effective_at <= self._cutoff
        ]

    def membership(self, index: str, on: date) -> frozenset[str]:
        return self._inner.membership(index, min(on, self._cutoff.date()))


def _same(a: Intent, b: Intent, tolerance: float) -> bool:
    if a.asof != b.asof or set(a.weights) != set(b.weights):
        return False
    return all(math.isclose(a.weights[s], b.weights[s], abs_tol=tolerance) for s in a.weights)


def check_lookahead(
    strategy: Strategy,
    provider: DataProvider,
    symbols: Sequence[str],
    check_times: Sequence[datetime],
    *,
    tolerance: float = DEFAULT_WEIGHT_TOLERANCE,
) -> StageResult:
    def fail(reason: str, checked: int = 0, mismatched: int = 0) -> StageResult:
        return StageResult(
            stage=Stage.LOOKAHEAD,
            passed=False,
            reason=reason,
            metrics={"n_checked": float(checked), "n_mismatch": float(mismatched)},
        )

    if not check_times:
        return fail("no check times given; nothing was tested")

    final_ts = max((b.ts for b in provider.bars(symbols, FAR_FUTURE)), default=None)
    if final_ts is None or not any(ensure_utc(t) < final_ts for t in check_times):
        return fail("vacuous: no check time has later data to hide, so a leak could not show")

    mismatched = 0
    for t in check_times:
        t = ensure_utc(t)
        try:
            full = strategy.generate(DataView(provider, t), t)
            cut = strategy.generate(DataView(TruncatedProvider(provider, t), t), t)
        except LookaheadError as err:
            return fail(
                f"lookahead: strategy tried to read the future at {t.isoformat()}: {err}", 1, 1
            )
        if not _same(full, cut, tolerance):
            mismatched += 1
    if mismatched:
        return fail(
            f"signals differ with and without future data at {mismatched} of "
            f"{len(check_times)} check times",
            len(check_times),
            mismatched,
        )
    return StageResult(
        stage=Stage.LOOKAHEAD,
        passed=True,
        reason=f"signals identical on truncated data at all {len(check_times)} check times",
        metrics={"n_checked": float(len(check_times)), "n_mismatch": 0.0},
    )
