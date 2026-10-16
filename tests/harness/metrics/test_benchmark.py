import numpy as np
import pytest

from harness.metrics.benchmark import beta_alpha


def _spy(n: int = 500, seed: int = 1) -> list[float]:
    return [float(x) for x in np.random.default_rng(seed).normal(0.0004, 0.01, n)]


def test_pure_beta_series_has_no_alpha() -> None:
    spy = _spy()
    strat = [1.5 * r for r in spy]
    out = beta_alpha(strat, spy)
    assert out.beta == pytest.approx(1.5)
    assert out.alpha_annual == pytest.approx(0.0, abs=1e-9)
    assert out.r_squared == pytest.approx(1.0)


def test_constant_extra_return_shows_as_alpha() -> None:
    spy = _spy()
    strat = [0.5 * r + 0.001 for r in spy]
    out = beta_alpha(strat, spy)
    assert out.beta == pytest.approx(0.5)
    assert out.alpha_daily == pytest.approx(0.001)
    assert out.alpha_annual == pytest.approx(0.252)


def test_cash_has_zero_beta_and_zero_alpha() -> None:
    out = beta_alpha([0.0] * 100, _spy(100))
    assert out.beta == pytest.approx(0.0)
    assert out.alpha_annual == pytest.approx(0.0)


def test_residual_series_removes_market() -> None:
    spy = _spy()
    out = beta_alpha([1.2 * r for r in spy], spy)
    assert max(abs(x) for x in out.residual_returns) < 1e-12


def test_constant_benchmark_is_refused() -> None:
    with pytest.raises(ValueError, match="variance"):
        beta_alpha([0.01, 0.02, 0.0], [0.01, 0.01, 0.01])


def test_length_mismatch_is_refused() -> None:
    with pytest.raises(ValueError, match="align"):
        beta_alpha([0.01, 0.02], [0.01])


def test_too_short_is_refused() -> None:
    with pytest.raises(ValueError, match="at least"):
        beta_alpha([0.01], [0.01])
