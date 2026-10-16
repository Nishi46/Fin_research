from datetime import date

import pytest

from contracts.data import CorporateAction, CorporateActionKind
from contracts.dataview import DataView
from harness.data.crosscheck import StooqCrossChecker
from harness.data.factories import PROVIDER_FACTORIES
from harness.data.membership import IndexHistory, PointInTimeMembership
from harness.data.provider import ComposedDataProvider
from harness.data.reconcile import ReconcileConfig, SnapshotRejected
from tests.data_fixtures import (
    load_text,
    normal_series,
    raw_split_series,
    series,
    split_ex_date,
    stale_series,
)


def _split() -> CorporateAction:
    return CorporateAction(
        symbol="SPL", kind=CorporateActionKind.SPLIT, effective_at=split_ex_date(), value=2.0
    )


def test_factory_table_registered() -> None:
    assert "composed" in PROVIDER_FACTORIES


def test_bars_are_adjusted_and_snapshot_is_biased() -> None:
    raw = raw_split_series()
    p = ComposedDataProvider(raw, corporate_actions=[_split()])
    bars = p.bars(["SPL"], raw[-1].ts)
    assert bars[0].close == pytest.approx(50.0)
    assert p.snapshot().survivorship_biased is True


def test_adjustment_is_point_in_time() -> None:
    raw = raw_split_series()
    p = ComposedDataProvider(raw, corporate_actions=[_split()])
    before_split = p.bars(["SPL"], raw[2].ts)
    assert before_split[0].close == raw[0].close  # split not yet known at that end


def test_dataview_blocks_future_over_composed_provider() -> None:
    raw = normal_series()
    view = DataView(ComposedDataProvider(raw), asof=raw[5].ts)
    assert view.bars(["AAA"])[-1].ts == raw[5].ts


def test_cross_check_disagreement_rejects_snapshot() -> None:
    bars = series("AAA", [100.0, 100.5, 101.0], start=date(2026, 1, 5))
    bad = (
        load_text("stooq_aaa.csv")
        .replace("100.5,1400000", "108.0,1400000")
        .replace("100.8", "108.1")
    )
    p = ComposedDataProvider(
        bars,
        cross_checkers=[StooqCrossChecker(lambda s, a, b: bad)],
        reconcile_config=ReconcileConfig(max_disagree_fraction=0.1),
    )
    with pytest.raises(SnapshotRejected):
        p.snapshot()


def test_cross_check_agreement_allows_snapshot() -> None:
    bars = series("AAA", [100.0, 100.5, 101.0], start=date(2026, 1, 5))
    p = ComposedDataProvider(
        bars, cross_checkers=[StooqCrossChecker(lambda s, a, b: load_text("stooq_aaa.csv"))]
    )
    assert p.snapshot().id.startswith("sha256:")


def test_strict_mode_rejects_unrecorded_split_and_stale_bars() -> None:
    with pytest.raises(SnapshotRejected):
        ComposedDataProvider(raw_split_series(), strict=True).snapshot()
    with pytest.raises(SnapshotRejected):
        ComposedDataProvider(stale_series(), strict=True).snapshot()


def test_non_strict_reports_issues_without_rejecting() -> None:
    p = ComposedDataProvider(raw_split_series())
    assert any(i.kind == "unrecorded_split" for i in p.quality_issues)
    p.snapshot()


def test_membership_delegates() -> None:
    pit = PointInTimeMembership({"X": IndexHistory(current=frozenset({"AAA"}), changes=())})
    p = ComposedDataProvider(normal_series(), membership=pit)
    assert p.membership("X", date(2026, 1, 5)) == frozenset({"AAA"})
