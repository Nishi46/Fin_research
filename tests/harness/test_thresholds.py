from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from harness.api import Regime
from harness.thresholds import DEFAULT_THRESHOLDS, Thresholds


def test_defaults_match_spec_r5() -> None:
    t = DEFAULT_THRESHOLDS
    assert t.embargo_min_bars == 1
    assert t.dsr_min_probability == 0.95
    assert t.pbo_max == 0.5
    assert t.cost_multiplier == 2.0
    assert t.cost_min_profit_factor == 1.0
    assert t.stability_tolerance == 0.20
    assert t.min_trades == 100
    assert t.min_profitable_regimes == 2


def test_pipeline_defaults() -> None:
    t = DEFAULT_THRESHOLDS
    assert t.wf_n_splits == 5
    assert t.wf_min_oos_sharpe == 0.0
    assert t.wf_min_positive_fold_fraction == 0.6
    assert t.stability_neighbor_fractions == (0.10, 0.20)
    assert t.lookahead_check_count == 20


def test_regime_derivation_settings() -> None:
    assert DEFAULT_THRESHOLDS.benchmark_symbol == "SPY"
    assert DEFAULT_THRESHOLDS.regime_min_drawdown == 0.15


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("regime_min_drawdown", 0.0),
        ("regime_min_drawdown", 1.0),
        ("regime_min_drawdown", -0.1),
        ("benchmark_symbol", ""),
        ("benchmark_symbol", "  "),
        ("wf_n_splits", 1),
        ("wf_min_positive_fold_fraction", 0.0),
        ("wf_min_positive_fold_fraction", 1.5),
        ("stability_neighbor_fractions", ()),
        ("stability_neighbor_fractions", (0.0, 0.1)),
        ("stability_neighbor_fractions", (0.1, 1.0)),
        ("lookahead_check_count", 0),
        ("cost_default_spread_bps", -1.0),
        ("cost_slippage_bps", -0.1),
        ("cost_commission_per_share", -0.01),
        ("cost_impact_bps_at_full_adv", -5.0),
        ("max_adv_participation", 0.0),
        ("max_adv_participation", 1.5),
        ("dsr_min_probability", 1.5),
        ("dsr_min_probability", 0.0),
        ("pbo_max", 1.5),
        ("pbo_min_trials", 1),
        ("pbo_min_trials", 9),
        ("pbo_max_peers", 0),
        ("pbo_n_blocks", 0),
        ("pbo_n_blocks", 15),
        ("cost_multiplier", 1.0),
        ("stability_tolerance", 0.0),
        ("min_trades", 0),
        ("min_profitable_regimes", 0),
        ("embargo_min_bars", 0),
    ],
)
def test_nonsensical_thresholds_rejected(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        Thresholds(**{field: value})


def test_cost_defaults_are_frozen_here() -> None:
    t = DEFAULT_THRESHOLDS
    assert t.cost_default_spread_bps == 2.0
    assert t.cost_slippage_bps == 1.0
    assert t.cost_commission_per_share == 0.0
    assert t.cost_impact_bps_at_full_adv == 150.0
    assert t.max_adv_participation == 0.05


def test_holdout_requires_a_t_statistic_not_a_fixed_sharpe() -> None:
    # A fixed Sharpe is a weaker bar on a short holdout than on a long one; a t-statistic scales.
    assert DEFAULT_THRESHOLDS.holdout_min_t_stat == 2.0
    assert not hasattr(DEFAULT_THRESHOLDS, "holdout_min_sharpe")


def test_holdout_length_and_query_cap_are_frozen_here() -> None:
    # Owner decision 2026-10-18: a 2-year holdout from the most recent data, 20 lifetime queries.
    assert DEFAULT_THRESHOLDS.holdout_years == 2
    assert DEFAULT_THRESHOLDS.holdout_max_queries == 20


def test_holdout_carve_out_settings_are_frozen_here() -> None:
    # Owner approved 2026-10-18.
    t = DEFAULT_THRESHOLDS
    assert t.holdout_embargo_sessions == 10
    assert t.holdout_min_session_fraction == 0.9
    assert t.holdout_min_research_years == 3


def test_reconciliation_tolerances_are_frozen_here() -> None:
    t = DEFAULT_THRESHOLDS
    assert t.reconcile_price_tolerance == 0.01
    # Calibrated 2026-10-18 from Alpaca (IEX) vs yfinance on 10 large caps over 3 years: 4 of 7500
    # bars (0.05%) differed by more than 1%.
    assert t.reconcile_max_disagree_fraction == 0.005
    assert t.reconcile_min_overlap == 0.9


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("holdout_min_t_stat", 0.0),
        ("holdout_min_t_stat", -1.0),
        ("holdout_years", 0),
        ("holdout_max_queries", 0),
        ("holdout_embargo_sessions", 0),
        ("holdout_min_session_fraction", 0.0),
        ("holdout_min_session_fraction", 1.5),
        ("holdout_min_research_years", 0),
        ("reconcile_price_tolerance", 0.0),
        ("reconcile_price_tolerance", 1.0),
        ("reconcile_max_disagree_fraction", -0.01),
        ("reconcile_max_disagree_fraction", 1.0),
        ("reconcile_min_overlap", 0.0),
        ("reconcile_min_overlap", 1.5),
    ],
)
def test_nonsensical_holdout_and_reconciliation_thresholds_rejected(
    field: str, value: float
) -> None:
    with pytest.raises(ValidationError):
        Thresholds(**{field: value})


def test_thresholds_are_immutable() -> None:
    with pytest.raises(ValidationError):
        DEFAULT_THRESHOLDS.min_trades = 1  # type: ignore[misc]


def test_unknown_fields_are_rejected_not_silently_ignored() -> None:
    # A typo, or an attempt to supply the old hand-written regime table, must fail loudly.
    with pytest.raises(ValidationError):
        Thresholds(pbo_maxx=0.9)  # type: ignore[call-arg]
    with pytest.raises(ValidationError):
        Thresholds(regimes=())  # type: ignore[call-arg]


def test_the_config_holds_rules_not_a_regime_table() -> None:
    assert not hasattr(DEFAULT_THRESHOLDS, "regimes")


def test_regime_lookup_is_half_open() -> None:
    regime = Regime(
        name="r", start=datetime(2020, 2, 19, tzinfo=UTC), end=datetime(2020, 3, 23, tzinfo=UTC)
    )
    assert regime.contains(regime.start)
    assert not regime.contains(regime.end)


def test_cross_trial_pbo_settings_are_frozen_here() -> None:
    t = DEFAULT_THRESHOLDS
    assert t.pbo_min_trials == 30
    assert t.pbo_max_peers == 200
    assert t.pbo_n_blocks == 16


def test_pbo_gate_must_be_reachable() -> None:
    # The candidate plus its peers is the group; a peer cap below min_trials - 1 never gates.
    with pytest.raises(ValidationError, match="reachable"):
        Thresholds(pbo_min_trials=30, pbo_max_peers=10)


def test_pbo_min_trials_lower_bound_is_ten() -> None:
    assert Thresholds(pbo_min_trials=10, pbo_max_peers=200).pbo_min_trials == 10
