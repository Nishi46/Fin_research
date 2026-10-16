import pytest

from harness.data.reconcile import (
    ReconcileConfig,
    SnapshotRejected,
    assert_reconciled,
    reconcile,
)
from tests.data_fixtures import disagreeing_source, normal_series


def test_identical_sources_reconcile() -> None:
    bars = normal_series()
    report = reconcile(bars, list(bars), source="other")
    assert report.ok
    assert report.compared == len(bars)
    assert report.disagreements == []


def test_one_disagreeing_bar_within_fraction_still_ok_but_listed() -> None:
    bars = normal_series(n=60)
    report = reconcile(
        bars,
        disagreeing_source(bars),
        source="other",
        config=ReconcileConfig(max_disagree_fraction=0.05),
    )
    assert report.ok
    assert len(report.disagreements) == 1


def test_disagreement_beyond_threshold_fails() -> None:
    bars = normal_series(n=20)
    report = reconcile(
        bars,
        disagreeing_source(bars),
        source="other",
        config=ReconcileConfig(max_disagree_fraction=0.01),
    )
    assert not report.ok
    with pytest.raises(SnapshotRejected, match="other"):
        assert_reconciled([report])


def test_insufficient_overlap_fails() -> None:
    bars = normal_series(n=20)
    report = reconcile(bars, bars[:5], source="thin", config=ReconcileConfig(min_overlap=0.9))
    assert not report.ok


def test_no_overlap_fails() -> None:
    report = reconcile(normal_series("AAA"), normal_series("BBB"), source="x")
    assert not report.ok
    assert report.compared == 0
