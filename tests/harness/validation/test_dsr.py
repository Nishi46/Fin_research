"""Deflated Sharpe Ratio tests.

Formulas (Bailey and Lopez de Prado, "The Deflated Sharpe Ratio", 2014), per-period Sharpe:

    SR0 = sqrt(V) * ((1 - g) * z(1 - 1/N) + g * z(1 - 1/(N e)))        g = Euler-Mascheroni
    DSR = Phi((SR - SR0) * sqrt(T - 1) / sqrt(1 - skew * SR + (kurt - 1) / 4 * SR^2))

Hand-derived reference values used below:

* N = 1: SR0 = 0 and DSR is the Probabilistic Sharpe Ratio. SR = 0.1, T = 101, normal returns:
  z = 0.1 * 10 / sqrt(1 + 0.5 * 0.01) = 0.99751, Phi(0.99751) = 0.8407.
* N = 100, V = 1: the bracket is 0.42278 * 2.32635 + 0.57722 * 2.68160 = 2.5314, close to the
  true expected maximum of 100 standard normals (about 2.508). It is the published approximation.
* Skew and kurtosis enter only through the denominator: SR = 0.1, skew = -1, kurt = 5 gives
  1 + 0.1 + 0.01 = 1.11, so z = 1.0 / sqrt(1.11) = 0.94916 and Phi = 0.8287.
"""

import uuid
from datetime import UTC, datetime, timedelta

import numpy as np
import pytest

from contracts import CapitalConfig, Stage
from fakes.registry import InMemoryRegistry
from harness.api import BacktestResult
from harness.thresholds import DEFAULT_THRESHOLDS, Thresholds
from harness.validation.dsr import (
    deflated_sharpe_ratio,
    expected_max_sharpe,
    sharpe_moments,
    trial_adjusted_stage,
    trial_sharpe_variance,
)
from tests.helpers import make_trial

T_START = datetime(2020, 1, 1, tzinfo=UTC)


def test_single_trial_is_the_probabilistic_sharpe_ratio() -> None:
    dsr = deflated_sharpe_ratio(0.1, n_obs=101, n_trials=1, sr_variance=1.0)
    assert dsr == pytest.approx(0.8407, abs=1e-4)


def test_expected_max_matches_the_published_approximation() -> None:
    sr0 = expected_max_sharpe(100, 1.0)
    assert sr0 == pytest.approx(2.5314, abs=1e-3)
    assert abs(sr0 - 2.508) < 0.03  # true E[max of 100 N(0,1)]
    assert expected_max_sharpe(1, 1.0) == 0.0
    assert expected_max_sharpe(100, 0.25) == pytest.approx(0.5 * 2.5314, abs=1e-3)


def test_skew_and_kurtosis_only_change_the_denominator() -> None:
    dsr = deflated_sharpe_ratio(
        0.1, n_obs=101, n_trials=1, sr_variance=1.0, skew=-1.0, kurtosis=5.0
    )
    assert dsr == pytest.approx(0.8287, abs=1e-4)


def test_more_trials_never_help() -> None:
    values = [
        deflated_sharpe_ratio(0.15, n_obs=500, n_trials=n, sr_variance=0.002)
        for n in (1, 2, 10, 100, 1000)
    ]
    assert values == sorted(values, reverse=True)
    assert values[0] > values[-1]


def test_higher_sharpe_helps_and_zero_trials_is_rejected() -> None:
    low = deflated_sharpe_ratio(0.05, n_obs=500, n_trials=10, sr_variance=0.002)
    high = deflated_sharpe_ratio(0.15, n_obs=500, n_trials=10, sr_variance=0.002)
    assert high > low
    with pytest.raises(ValueError, match="n_trials"):
        deflated_sharpe_ratio(0.1, n_obs=500, n_trials=0, sr_variance=0.002)
    with pytest.raises(ValueError, match="n_obs"):
        deflated_sharpe_ratio(0.1, n_obs=1, n_trials=1, sr_variance=0.002)


def test_sharpe_moments_of_a_known_series() -> None:
    r = np.array([1.0, -1.0, 1.0, -1.0])
    sr, skew, kurt = sharpe_moments(r)
    assert sr == 0.0
    assert skew == pytest.approx(0.0)
    assert kurt == pytest.approx(1.0)  # raw (not excess) kurtosis of a symmetric two-point law


@pytest.mark.parametrize("seed", range(25))
def test_best_of_many_noise_strategies_is_not_significant(seed: int) -> None:
    """The core guarantee: selection from pure noise must not look like skill."""
    n_strategies, n_obs = 200, 500
    rng = np.random.default_rng(seed)
    returns = rng.normal(0.0, 0.01, size=(n_obs, n_strategies))
    srs = returns.mean(axis=0) / returns.std(axis=0, ddof=1)
    best = int(np.argmax(srs))
    sr, skew, kurt = sharpe_moments(returns[:, best])
    var = float(np.var(srs, ddof=1))
    dsr = deflated_sharpe_ratio(
        sr, n_obs=n_obs, n_trials=n_strategies, sr_variance=var, skew=skew, kurtosis=kurt
    )
    naive = deflated_sharpe_ratio(
        sr, n_obs=n_obs, n_trials=1, sr_variance=var, skew=skew, kurtosis=kurt
    )
    assert dsr < DEFAULT_THRESHOLDS.dsr_min_probability
    assert naive > 0.95  # without deflation the same winner would look significant


def test_a_genuine_edge_survives_deflation() -> None:
    dsr = deflated_sharpe_ratio(0.15, n_obs=1000, n_trials=10, sr_variance=0.001)
    assert dsr > 0.99


# --- stage -----------------------------------------------------------------------------------


def result_from(returns: np.ndarray) -> BacktestResult:
    ts = tuple(T_START + timedelta(days=i) for i in range(len(returns)))
    return BacktestResult(
        capital=CapitalConfig(effective_at=T_START, source="default"),
        snapshot_id="snap",
        cost_multiplier=1.0,
        seed=0,
        code_version="v",
        survivorship_biased=True,
        timestamps=ts,
        daily_returns=tuple(float(x) for x in returns),
        trades=(),
    )


def registry_with(n: int, sharpes: list[float] | None = None) -> InMemoryRegistry:
    reg = InMemoryRegistry()
    for i in range(n):
        metrics = {} if sharpes is None else {"sharpe_per_period": sharpes[i]}
        reg.append(make_trial(id=uuid.uuid4(), metrics=metrics))
    return reg


def test_stage_uses_the_registry_count_at_evaluation_time() -> None:
    rng = np.random.default_rng(1)
    result = result_from(rng.normal(0.0008, 0.01, size=750))
    few = trial_adjusted_stage(result, registry_with(1))
    many = trial_adjusted_stage(result, registry_with(500))
    assert few.stage is Stage.TRIAL_ADJUSTED
    assert few.metrics["n_trials"] == 1
    assert many.metrics["n_trials"] == 500
    assert many.metrics["dsr"] < few.metrics["dsr"]


def test_stage_rejects_best_of_noise_and_passes_a_strong_edge() -> None:
    rng = np.random.default_rng(7)
    noise = rng.normal(0.0, 0.01, size=(500, 200))
    srs = noise.mean(axis=0) / noise.std(axis=0, ddof=1)
    best = noise[:, int(np.argmax(srs))]
    reg = registry_with(200, [float(s) for s in srs])
    verdict = trial_adjusted_stage(result_from(best), reg)
    assert not verdict.passed
    assert "dsr" in verdict.reason.lower()

    edge = rng.normal(0.004, 0.01, size=1000)
    assert trial_adjusted_stage(result_from(edge), registry_with(5, [0.02] * 5)).passed


def test_stage_threshold_is_injectable() -> None:
    rng = np.random.default_rng(3)
    result = result_from(rng.normal(0.0006, 0.01, size=750))
    reg = registry_with(1)
    lenient = Thresholds(dsr_min_probability=0.01)
    strict = Thresholds(dsr_min_probability=0.999999)
    assert trial_adjusted_stage(result, reg, thresholds=lenient).passed
    assert not trial_adjusted_stage(result, reg, thresholds=strict).passed


def test_tiny_or_flat_samples_fail_without_a_sharpe() -> None:
    reg = registry_with(1)
    for returns in (np.array([0.01]), np.zeros(100)):
        verdict = trial_adjusted_stage(result_from(returns), reg)
        assert not verdict.passed
        assert "dsr" not in verdict.metrics


def test_trial_sharpe_variance_reads_registry_metrics() -> None:
    reg = registry_with(4, [0.0, 0.1, 0.2, 0.3])
    assert trial_sharpe_variance(reg) == pytest.approx(float(np.var([0, 0.1, 0.2, 0.3], ddof=1)))
    assert trial_sharpe_variance(registry_with(3)) is None  # no sharpe recorded
    assert trial_sharpe_variance(registry_with(1, [0.1])) is None  # need two to get a variance


def test_stage_gates_on_pbo_when_trial_returns_are_given() -> None:
    rng = np.random.default_rng(11)
    edge = rng.normal(0.004, 0.01, size=800)
    noise_matrix = rng.normal(0.0, 0.01, size=(1600, 12))
    lenient = trial_adjusted_stage(
        result_from(edge),
        registry_with(1),
        trial_returns=noise_matrix,
        pbo_gating=True,
        thresholds=Thresholds(pbo_max=1.0),
    )
    assert lenient.passed
    assert 0.0 <= lenient.metrics["pbo"] <= 1.0
    bad = trial_adjusted_stage(
        result_from(edge),
        registry_with(1),
        trial_returns=noise_matrix,
        pbo_gating=True,
        thresholds=Thresholds(pbo_max=0.01),
    )
    assert not bad.passed
    assert "pbo" in bad.reason.lower()


def test_explicit_trial_count_overrides_the_registry_count() -> None:
    rng = np.random.default_rng(1)
    result = result_from(rng.normal(0.0008, 0.01, size=750))
    verdict = trial_adjusted_stage(result, registry_with(4), n_trials=5)
    assert verdict.metrics["n_trials"] == 5


def test_trials_without_a_real_sharpe_are_ignored_for_the_variance() -> None:
    reg = InMemoryRegistry()
    for sharpe, missing in [(0.0, 1.0), (0.1, 0.0), (0.3, 0.0), (0.0, 1.0)]:
        metrics = {"sharpe_per_period": sharpe, "sharpe_per_period_missing": missing}
        reg.append(make_trial(id=uuid.uuid4(), metrics=metrics))
    assert trial_sharpe_variance(reg) == pytest.approx(float(np.var([0.1, 0.3], ddof=1)))


def test_pbo_gates_only_when_strictly_above_the_maximum_and_opted_in() -> None:
    rng = np.random.default_rng(2)
    edge = rng.normal(0.004, 0.01, size=800)
    matrix = rng.normal(0.0, 0.01, size=(1600, 6))
    from harness.validation.pbo import probability_of_backtest_overfitting

    pbo = probability_of_backtest_overfitting(matrix)
    at = trial_adjusted_stage(
        result_from(edge),
        registry_with(1),
        trial_returns=matrix,
        pbo_gating=True,
        thresholds=Thresholds(pbo_max=pbo),
    )
    assert at.passed  # strictly above is required to fail
    below = trial_adjusted_stage(
        result_from(edge),
        registry_with(1),
        trial_returns=matrix,
        pbo_gating=True,
        thresholds=Thresholds(pbo_max=pbo - 0.01),
    )
    assert not below.passed
    assert "pbo" in below.reason.lower()
    assert below.metrics["pbo_computed"] == 1.0
    assert below.metrics["pbo_gating"] == 1.0


def test_pbo_that_cannot_be_computed_fails_only_when_gating() -> None:
    rng = np.random.default_rng(2)
    edge = rng.normal(0.004, 0.01, size=800)
    too_short = rng.normal(0.0, 0.01, size=(10, 4))
    gating = trial_adjusted_stage(
        result_from(edge), registry_with(1), trial_returns=too_short, pbo_gating=True
    )
    assert not gating.passed
    assert "PBO cannot be computed" in gating.reason
    assert gating.metrics["pbo_computed"] == 0.0
    informational = trial_adjusted_stage(
        result_from(edge), registry_with(1), trial_returns=too_short
    )
    assert informational.passed  # not gating: an uncomputable PBO does not fail the stage
    assert informational.metrics["pbo_computed"] == 0.0
    assert informational.metrics["pbo_gating"] == 0.0


def test_pbo_computed_is_zero_when_no_matrix_is_given() -> None:
    rng = np.random.default_rng(2)
    verdict = trial_adjusted_stage(result_from(rng.normal(0.004, 0.01, size=800)), registry_with(1))
    assert verdict.metrics["pbo_computed"] == 0.0


def test_pbo_is_informational_by_default() -> None:
    rng = np.random.default_rng(11)
    edge = rng.normal(0.004, 0.01, size=800)
    noise_matrix = rng.normal(0.0, 0.01, size=(1600, 12))
    verdict = trial_adjusted_stage(
        result_from(edge),
        registry_with(1),
        trial_returns=noise_matrix,
        thresholds=Thresholds(pbo_max=0.01),
    )
    assert verdict.passed
    assert verdict.metrics["pbo"] > 0.01
    assert verdict.metrics["pbo_computed"] == 1.0
    assert verdict.metrics["pbo_gating"] == 0.0
