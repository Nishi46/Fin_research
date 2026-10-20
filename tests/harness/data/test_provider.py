from datetime import date

import pytest

from contracts.data import CorporateAction, CorporateActionKind
from contracts.dataview import DataView
from harness.data.common import SpinOff
from harness.data.crosscheck import StooqCrossChecker, YFinanceCrossChecker
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


def _yahoo_rows(bars: list) -> list[dict[str, object]]:  # type: ignore[type-arg]
    """yfinance with auto_adjust=False: split-adjusted closes, dividends NOT applied."""
    return [
        {
            "Date": b.ts.date().isoformat(),
            "Open": b.open,
            "High": b.high,
            "Low": b.low,
            "Close": b.close,
            "Volume": b.volume,
        }
        for b in bars
    ]


def test_reconciliation_ignores_dividends_because_yahoo_close_is_not_dividend_adjusted() -> None:
    # Regression: the real carve compared dividend-adjusted prices with Yahoo's split-only Close,
    # so every bar before a dividend looked wrong and 76% of bars "disagreed" on real data.
    bars = series("AAA", [100.0] * 40, start=date(2026, 1, 5))
    dividend = CorporateAction(
        symbol="AAA", kind=CorporateActionKind.DIVIDEND, effective_at=bars[30].ts, value=3.0
    )
    checker = YFinanceCrossChecker(lambda s, a, b: _yahoo_rows(bars))
    provider = ComposedDataProvider(bars, corporate_actions=[dividend], cross_checkers=[checker])
    assert provider.snapshot().id.startswith("sha256:")


def test_reconciliation_still_applies_splits() -> None:
    raw = raw_split_series()
    split_adjusted = ComposedDataProvider(raw, corporate_actions=[_split()]).bars(
        ["SPL"], raw[-1].ts
    )
    checker = YFinanceCrossChecker(lambda s, a, b: _yahoo_rows(list(split_adjusted)))
    provider = ComposedDataProvider(raw, corporate_actions=[_split()], cross_checkers=[checker])
    assert provider.snapshot().id.startswith("sha256:")


def test_reconciliation_still_rejects_a_real_disagreement_with_dividends_present() -> None:
    bars = series("AAA", [100.0] * 40, start=date(2026, 1, 5))
    dividend = CorporateAction(
        symbol="AAA", kind=CorporateActionKind.DIVIDEND, effective_at=bars[30].ts, value=3.0
    )
    wrong = [
        {**row, "Open": 120.0, "High": 120.0, "Low": 120.0, "Close": 120.0}
        for row in _yahoo_rows(bars)
    ]
    checker = YFinanceCrossChecker(lambda s, a, b: wrong)
    provider = ComposedDataProvider(bars, corporate_actions=[dividend], cross_checkers=[checker])
    with pytest.raises(SnapshotRejected):
        provider.snapshot()


def _spin_off(symbol: str, ex_date: date) -> SpinOff:
    return SpinOff(source_symbol=symbol, ex_date=ex_date, new_symbol="NEWCO", new_rate=0.25)


def test_strict_rejects_spin_off_inside_range_and_names_symbols() -> None:
    bars = [*normal_series("GE"), *normal_series("HON"), *normal_series("CLEAN")]
    mid = bars[10].ts.date()
    p = ComposedDataProvider(
        bars, strict=True, spin_offs=[_spin_off("HON", mid), _spin_off("GE", mid)]
    )
    assert [i.kind for i in p.quality_issues] == ["spin_off", "spin_off"]
    with pytest.raises(SnapshotRejected, match=r"spin_off \(GE, HON\)"):
        p.snapshot()


def test_strict_passes_spin_off_outside_range_or_unknown_symbol() -> None:
    bars = normal_series("AAA")
    p = ComposedDataProvider(
        bars,
        strict=True,
        spin_offs=[_spin_off("AAA", date(2030, 1, 2)), _spin_off("ZZZ", bars[5].ts.date())],
    )
    assert p.quality_issues == []
    assert p.snapshot().id.startswith("sha256:")


def test_non_strict_keeps_spin_off_issue_but_snapshots() -> None:
    bars = normal_series("AAA")
    p = ComposedDataProvider(bars, spin_offs=[_spin_off("AAA", bars[10].ts.date())])
    assert [i.kind for i in p.quality_issues] == ["spin_off"]
    assert p.snapshot().id.startswith("sha256:")


def test_strict_message_keeps_plain_kinds_format_for_other_issues() -> None:
    with pytest.raises(
        SnapshotRejected, match=r"data quality issues: suspect_jump, unrecorded_split$"
    ):
        ComposedDataProvider(raw_split_series(), strict=True).snapshot()
