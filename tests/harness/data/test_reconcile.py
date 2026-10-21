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


def test_symbol_with_zero_overlap_fails_even_when_pooled_overlap_is_fine() -> None:
    covered = [b for s in ("AAA", "BBB", "CCC") for b in normal_series(s, n=40)]
    uncovered = normal_series("BRK.B", n=5)
    other = [*covered, *normal_series("BRK-B", n=5)]  # symbol-format mismatch: no key matches
    report = reconcile([*covered, *uncovered], other, source="yahoo")
    assert report.compared == len(covered)
    assert not report.ok
    assert "no overlap for: BRK.B" in report.reason


def test_zero_overlap_reason_names_all_uncovered_symbols_sorted() -> None:
    covered = [b for s in ("AAA", "BBB", "CCC") for b in normal_series(s, n=40)]
    primary = [*covered, *normal_series("ZZZ", n=2), *normal_series("MMM", n=2)]
    report = reconcile(primary, covered, source="yahoo")
    assert not report.ok
    assert "no overlap for: MMM, ZZZ" in report.reason


def test_all_symbols_covered_is_still_ok() -> None:
    bars = [b for s in ("AAA", "BBB") for b in normal_series(s, n=30)]
    report = reconcile(bars, list(bars), source="yahoo")
    assert report.ok
    assert report.reason == ""


def test_partially_covered_symbol_does_not_trip_the_per_symbol_rule() -> None:
    full = normal_series("AAA", n=100)
    thin = normal_series("BBB", n=10)
    report = reconcile([*full, *thin], [*full, *thin[:1]], source="yahoo")  # 101/110 > 90%
    assert report.ok
