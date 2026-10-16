"""PBO via CSCV (Bailey, Borwein, Lopez de Prado and Zhu, 2015).

Hand-derived cases (S = 2 blocks, so two combinations, N = 2 strategies, omega = rank / (N + 1)):

* A = [2, 1, -2, -1], B = [-2, -1, 2, 1]. Train on block 0: A wins in sample, then A is last out
  of sample (rank 1, omega = 1/3, logit < 0). Train on block 1: B wins, B is last out of sample.
  Both logits are negative, so PBO = 1.
* A = [2, 1, 2, 1], B = [-2, -1, -2, -1]. A wins in sample and out of sample (rank 2, omega =
  2/3, logit = ln 2 > 0) in both combinations, so PBO = 0.
"""

import numpy as np
import pytest

from harness.validation.pbo import probability_of_backtest_overfitting


def test_hand_derived_full_overfit() -> None:
    m = np.array([[2, -2], [1, -1], [-2, 2], [-1, 1]], dtype=float)
    assert probability_of_backtest_overfitting(m, n_blocks=2) == 1.0


def test_hand_derived_no_overfit() -> None:
    m = np.array([[2, -2], [1, -1], [2, -2], [1, -1]], dtype=float)
    assert probability_of_backtest_overfitting(m, n_blocks=2) == 0.0


def test_pure_noise_is_near_one_half() -> None:
    values = [
        probability_of_backtest_overfitting(
            np.random.default_rng(seed).normal(0, 0.01, size=(800, 10)), n_blocks=10
        )
        for seed in range(20)
    ]
    assert 0.4 < float(np.mean(values)) < 0.6


def test_a_persistent_edge_among_noise_is_not_overfit() -> None:
    rng = np.random.default_rng(5)
    m = rng.normal(0, 0.01, size=(1600, 10))
    m[:, 0] += 0.003
    assert probability_of_backtest_overfitting(m, n_blocks=16) < 0.1


def test_is_deterministic_and_ignores_leftover_rows() -> None:
    m = np.random.default_rng(0).normal(0, 0.01, size=(803, 6))
    a = probability_of_backtest_overfitting(m, n_blocks=8)
    assert a == probability_of_backtest_overfitting(m, n_blocks=8)


@pytest.mark.parametrize("n_blocks", [3, 0, -2])
def test_blocks_must_be_a_positive_even_number(n_blocks: int) -> None:
    with pytest.raises(ValueError, match="n_blocks"):
        probability_of_backtest_overfitting(np.zeros((100, 3)), n_blocks=n_blocks)


def test_needs_two_strategies_and_enough_rows() -> None:
    with pytest.raises(ValueError, match="two strategies"):
        probability_of_backtest_overfitting(np.zeros((100, 1)), n_blocks=4)
    with pytest.raises(ValueError, match="too few"):
        probability_of_backtest_overfitting(np.zeros((6, 3)), n_blocks=8)
