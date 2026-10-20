"""Composed DataProvider: raw bars + actions, adjusted point-in-time, quality-gated."""

from collections.abc import Sequence
from datetime import date, datetime
from typing import Protocol

from contracts.data import Bar, CorporateAction, CorporateActionKind, Snapshot
from harness.api import Regime
from harness.data.adjust import adjust_bars, find_unadjusted_jumps, find_unrecorded_splits
from harness.data.common import Issue, SpinOff
from harness.data.membership import PointInTimeMembership
from harness.data.quality import find_gaps, find_spin_offs, find_stale_bars
from harness.data.reconcile import (
    ReconcileConfig,
    ReconciliationReport,
    SnapshotRejected,
    assert_reconciled,
)
from harness.data.regimes import RegimeCoverage, coverage_report
from harness.data.snapshot import build_snapshot


class CrossChecker(Protocol):
    name: str

    def check(
        self, primary: Sequence[Bar], symbols: Sequence[str], config: ReconcileConfig
    ) -> ReconciliationReport: ...


class ComposedDataProvider:
    """`strict=True` makes quality findings (gaps, stale bars, unrecorded splits, jumps in the
    adjusted series) reject the snapshot. Cross-checker disagreement always rejects.
    Cross-checkers can only veto; there is no way to use one as the primary source."""

    def __init__(
        self,
        bars: Sequence[Bar],
        *,
        corporate_actions: Sequence[CorporateAction] = (),
        membership: PointInTimeMembership | None = None,
        cross_checkers: Sequence[CrossChecker] = (),
        reconcile_config: ReconcileConfig | None = None,
        strict: bool = False,
        spin_offs: Sequence[SpinOff] = (),
    ) -> None:
        self._raw = sorted(bars, key=lambda b: (b.ts, b.symbol))
        self._actions = sorted(corporate_actions, key=lambda a: (a.effective_at, a.symbol))
        self._membership = membership
        self._checkers = list(cross_checkers)
        self._config = reconcile_config or ReconcileConfig.from_thresholds()
        self._strict = strict
        self.quality_issues: list[Issue] = [
            *find_gaps(self._raw),
            *find_stale_bars(self._raw),
            *find_unrecorded_splits(self._raw, self._actions),
            *find_unadjusted_jumps(adjust_bars(self._raw, self._actions)),
            *find_spin_offs(self._raw, spin_offs),
        ]

    def snapshot(self) -> Snapshot:
        if self._strict and self.quality_issues:
            kinds = sorted({i.kind for i in self.quality_issues})
            labels = [
                f"{k} ({', '.join(sorted({i.symbol for i in self.quality_issues if i.kind == k}))})"
                if k == "spin_off"
                else k
                for k in kinds
            ]
            raise SnapshotRejected(f"data quality issues: {', '.join(labels)}")
        if self._checkers:
            # Cross-source closes (Yahoo with auto_adjust=False) are split-adjusted but not
            # dividend-adjusted, so compare on that basis; dividend-adjusted prices differ by the
            # cumulative dividends and would make most bars of a long history "disagree".
            splits = [a for a in self._actions if a.kind is CorporateActionKind.SPLIT]
            adjusted = adjust_bars(self._raw, splits)
            symbols = sorted({b.symbol for b in self._raw})
            assert_reconciled([c.check(adjusted, symbols, self._config) for c in self._checkers])
        return build_snapshot(
            self._raw,
            actions=self._actions,
            memberships=self._membership.fingerprint() if self._membership else None,
        )

    def bars(self, symbols: Sequence[str], end: datetime) -> list[Bar]:
        wanted = set(symbols)
        visible = [b for b in self._raw if b.symbol in wanted and b.ts <= end]
        return adjust_bars(visible, self._actions, end=end)

    def corporate_actions(self, symbols: Sequence[str], end: datetime) -> list[CorporateAction]:
        wanted = set(symbols)
        return [a for a in self._actions if a.symbol in wanted and a.effective_at <= end]

    def membership(self, index: str, on: date) -> frozenset[str]:
        return self._membership.membership(index, on) if self._membership else frozenset()

    def regime_coverage(self, regimes: Sequence[Regime], min_sessions: int) -> RegimeCoverage:
        return coverage_report(self._raw, regimes, min_sessions=min_sessions)
