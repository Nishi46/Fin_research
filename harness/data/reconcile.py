"""Cross-source reconciliation. Both inputs must be on the same adjustment basis."""

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date

from contracts.data import Bar
from harness.data.common import Issue
from harness.thresholds import DEFAULT_THRESHOLDS, Thresholds


class SnapshotRejected(Exception):
    """The data failed a quality gate, so no snapshot may be issued from it."""


@dataclass(frozen=True)
class ReconcileConfig:
    # To be calibrated from the distribution of real differences once two real sources exist.
    price_tolerance: float = DEFAULT_THRESHOLDS.reconcile_price_tolerance
    max_disagree_fraction: float = DEFAULT_THRESHOLDS.reconcile_max_disagree_fraction
    min_overlap: float = DEFAULT_THRESHOLDS.reconcile_min_overlap

    @classmethod
    def from_thresholds(cls, thresholds: Thresholds = DEFAULT_THRESHOLDS) -> "ReconcileConfig":
        return cls(
            price_tolerance=thresholds.reconcile_price_tolerance,
            max_disagree_fraction=thresholds.reconcile_max_disagree_fraction,
            min_overlap=thresholds.reconcile_min_overlap,
        )


@dataclass(frozen=True)
class ReconciliationReport:
    source: str
    compared: int
    primary_total: int
    disagreements: list[Issue] = field(default_factory=list)
    ok: bool = False
    reason: str = ""


def reconcile(
    primary: Sequence[Bar],
    other: Sequence[Bar],
    *,
    source: str,
    config: ReconcileConfig | None = None,
) -> ReconciliationReport:
    config = config or ReconcileConfig.from_thresholds()
    other_by_key: dict[tuple[str, date], Bar] = {(b.symbol, b.ts.date()): b for b in other}
    compared = 0
    disagreements: list[Issue] = []
    for bar in primary:
        match = other_by_key.get((bar.symbol, bar.ts.date()))
        if match is None:
            continue
        compared += 1
        if abs(bar.close / match.close - 1) > config.price_tolerance:
            disagreements.append(
                Issue(
                    "disagreement",
                    bar.symbol,
                    bar.ts,
                    f"{source} close {match.close} vs primary {bar.close}",
                )
            )
    total = len(primary)
    reason = ""
    if compared == 0:
        reason = "no overlapping bars"
    elif compared / total < config.min_overlap:
        reason = f"overlap {compared}/{total} below {config.min_overlap:.0%}"
    elif len(disagreements) / compared > config.max_disagree_fraction:
        reason = f"{len(disagreements)}/{compared} bars disagree beyond tolerance"
    return ReconciliationReport(
        source=source,
        compared=compared,
        primary_total=total,
        disagreements=disagreements,
        ok=reason == "",
        reason=reason,
    )


def assert_reconciled(reports: Sequence[ReconciliationReport]) -> None:
    failed = [r for r in reports if not r.ok]
    if failed:
        detail = "; ".join(f"{r.source}: {r.reason}" for r in failed)
        raise SnapshotRejected(f"sources disagree: {detail}")
