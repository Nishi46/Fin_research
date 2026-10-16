"""Task 38: pure-noise strategies, and the best of N looks good only by selection."""

import numpy as np
import pytest

from tests.adversarial._sim import best_of_n, noise_sharpes, simulate
from tests.adversarial.strategies.noise import NoiseStrategy
from tests.adversarial.worlds import noise_world

N_TRIALS = 200


@pytest.fixture(scope="module")
def world():
    return noise_world(7)


def test_vectorised_scoring_matches_the_real_strategy(world):
    seed = 5
    real = simulate(world, NoiseStrategy(world.symbols, seed), start=30, end=130)
    fast = noise_sharpes(world, [seed], start=30, end=130)[0]
    assert fast == pytest.approx(real.annual_sharpe(real.gross), rel=1e-9)


def test_noise_strategies_have_no_average_edge(world):
    sharpes = noise_sharpes(world, range(N_TRIALS))
    assert abs(float(np.mean(sharpes))) < 0.15


def test_best_of_n_looks_good_in_sample_but_not_out_of_sample(world):
    best_seed, best_sharpe = best_of_n(world, N_TRIALS)
    assert best_sharpe > 1.0  # the selection effect: a flattering number from pure noise
    fresh = noise_world(8)
    (out_of_sample,) = noise_sharpes(fresh, [best_seed])
    assert out_of_sample < best_sharpe / 2


def test_noise_world_is_generated_with_no_edge(world):
    assert world.truth == {}
    assert set(world.reversion) == {0.0}
