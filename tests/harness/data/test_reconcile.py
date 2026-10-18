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


def test_default_config_reads_the_frozen_thresholds() -> None:
    from harness.thresholds import DEFAULT_THRESHOLDS as T

    cfg = ReconcileConfig()
    assert cfg.price_tolerance == T.reconcile_price_tolerance
    assert cfg.max_disagree_fraction == T.reconcile_max_disagree_fraction
    assert cfg.min_overlap == T.reconcile_min_overlap
    assert cfg == ReconcileConfig.from_thresholds()


def test_config_from_injected_thresholds_and_explicit_override() -> None:
    from harness.thresholds import Thresholds

    custom = Thresholds(
        reconcile_price_tolerance=0.05,
        reconcile_max_disagree_fraction=0.1,
        reconcile_min_overlap=0.5,
    )
    cfg = ReconcileConfig.from_thresholds(custom)
    assert (cfg.price_tolerance, cfg.max_disagree_fraction, cfg.min_overlap) == (0.05, 0.1, 0.5)
    assert ReconcileConfig(price_tolerance=0.2).price_tolerance == 0.2
    assert ReconcileConfig(price_tolerance=0.2).min_overlap == ReconcileConfig().min_overlap


def test_reconcile_without_config_uses_threshold_defaults() -> None:
    bars = normal_series()
    assert reconcile(bars, list(bars), source="o") == reconcile(
        bars, list(bars), source="o", config=ReconcileConfig.from_thresholds()
    )
