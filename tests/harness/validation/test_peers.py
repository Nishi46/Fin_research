import uuid
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

import numpy as np
import pytest

from contracts import ReturnSeries
from fakes.registry import InMemoryRegistry
from harness.costs.model import RealCostModel, ScaledCostModel
from harness.registry.sqlite import SqliteRegistry
from harness.thresholds import DEFAULT_THRESHOLDS, Thresholds
from harness.validation.peers import cost_model_fingerprint, group_key, peer_matrix

START = datetime(2020, 1, 1, 21, tzinfo=UTC)
END = START + timedelta(days=149)

BASE: dict[str, Any] = {
    "snapshot_id": "snap",
    "start": START,
    "end": END,
    "cost_multiplier": 1.0,
    "capital_amount": Decimal("10000"),
    "capital_currency": "USD",
    "cost_fingerprint": cost_model_fingerprint(None),
}


def test_group_key_is_stable() -> None:
    assert group_key(**BASE) == group_key(**BASE)


@pytest.mark.parametrize(
    ("field", "changed"),
    [
        ("snapshot_id", "other"),
        ("start", START + timedelta(days=1)),
        ("end", END + timedelta(days=1)),
        ("cost_multiplier", 2.0),
        ("capital_amount", Decimal("10001")),
        ("capital_currency", "EUR"),
        ("cost_fingerprint", cost_model_fingerprint(RealCostModel(slippage_bps=9.0))),
    ],
)
def test_each_component_changes_the_key(field: str, changed: Any) -> None:
    assert group_key(**{**BASE, field: changed}) != group_key(**BASE)


def test_equal_instants_in_different_offsets_share_a_key() -> None:
    plus5 = timezone(timedelta(hours=5))
    assert group_key(
        **{**BASE, "start": START.astimezone(plus5), "end": END.astimezone(plus5)}
    ) == (group_key(**BASE))


@pytest.mark.parametrize("amount", [Decimal("10000.00"), Decimal("1E+4"), Decimal("10000.0")])
def test_equivalent_decimals_share_a_key(amount: Decimal) -> None:
    assert group_key(**{**BASE, "capital_amount": amount}) == group_key(**BASE)


def _series(returns: np.ndarray, group: str = "g", trial: uuid.UUID | None = None) -> ReturnSeries:
    return ReturnSeries(
        trial_id=trial or uuid.uuid4(),
        group_key=group,
        timestamps=tuple(START + timedelta(days=i) for i in range(len(returns))),
        returns=tuple(float(r) for r in returns),
    )


def test_candidate_identical_to_a_stored_peer_gives_identical_columns() -> None:
    registry = SqliteRegistry()  # stores float32, like production
    values = np.random.default_rng(1).normal(0.0004, 0.01, 60)
    registry.append_returns(_series(values))
    matrix = peer_matrix(registry, _series(values), max_peers=5)
    assert matrix.shape == (60, 2)
    assert np.array_equal(matrix[:, 0], matrix[:, 1])


def test_peer_cap_uses_the_most_recent_peers_and_never_the_candidate() -> None:
    registry = InMemoryRegistry()
    max_peers = 5
    stored = []
    for i in range(max_peers + 4):
        s = _series(np.full(20, 0.001 * (i + 1)))
        registry.append_returns(s)
        stored.append(s)
    candidate = _series(np.full(20, 0.5))
    registry.append_returns(candidate)  # the pipeline files the candidate before loading peers
    matrix = peer_matrix(registry, candidate, max_peers=max_peers)
    assert matrix.shape == (20, max_peers + 1)
    assert np.allclose(matrix[:, 0], 0.5)
    peer_levels = sorted(float(c) for c in matrix[0, 1:])
    expected = sorted(float(np.float32(s.returns[0])) for s in stored[-max_peers:])
    assert peer_levels == pytest.approx(expected)
    assert not np.any(np.isclose(matrix[0, 1:], 0.5))


# --- cost-model fingerprint ---------------------------------------------------------------------


def test_same_configuration_gives_the_same_fingerprint() -> None:
    assert cost_model_fingerprint(RealCostModel(slippage_bps=3.0)) == cost_model_fingerprint(
        RealCostModel(slippage_bps=3.0)
    )


@pytest.mark.parametrize(
    "override",
    [
        {"default_spread_bps": 7.0},
        {"slippage_bps": 7.0},
        {"commission_per_share": 0.5},
        {"impact_bps_at_full_adv": 99.0},
        {"max_adv_participation": 0.5},
    ],
)
def test_changing_any_cost_parameter_changes_the_fingerprint(override: dict[str, float]) -> None:
    assert cost_model_fingerprint(RealCostModel(**override)) != cost_model_fingerprint(
        RealCostModel()
    )


def test_none_equals_the_explicit_default_model() -> None:
    assert cost_model_fingerprint(None) == cost_model_fingerprint(RealCostModel())
    assert cost_model_fingerprint(None) == cost_model_fingerprint(RealCostModel(DEFAULT_THRESHOLDS))


def test_none_follows_the_frozen_default_thresholds_values() -> None:
    other = Thresholds(**{**DEFAULT_THRESHOLDS.model_dump(), "cost_slippage_bps": 77.0})
    assert cost_model_fingerprint(RealCostModel(other)) != cost_model_fingerprint(None)


class LookalikeCostModel:
    """Same numbers as RealCostModel's default, different class."""

    def __init__(self) -> None:
        self._default_spread_bps = RealCostModel().default_spread_bps
        self._slippage_bps = RealCostModel().slippage_bps


class LookalikeTwo(LookalikeCostModel):
    pass


def test_a_different_class_with_the_same_numbers_differs() -> None:
    assert cost_model_fingerprint(LookalikeCostModel()) != cost_model_fingerprint(LookalikeTwo())
    assert cost_model_fingerprint(LookalikeCostModel()) != cost_model_fingerprint(RealCostModel())


def test_a_wrapped_model_and_its_multiplier_are_both_included() -> None:
    inner = RealCostModel()
    scaled = cost_model_fingerprint(ScaledCostModel(inner, 2.0))
    assert scaled != cost_model_fingerprint(inner)
    assert scaled != cost_model_fingerprint(ScaledCostModel(inner, 3.0))
    assert scaled != cost_model_fingerprint(ScaledCostModel(RealCostModel(slippage_bps=9.0), 2.0))
    assert scaled == cost_model_fingerprint(ScaledCostModel(RealCostModel(), 2.0))


class Opaque:
    __slots__ = ()


class Slotted:
    __slots__ = ("x",)

    def __init__(self) -> None:
        self.x = 1


@pytest.mark.parametrize("model", [Opaque(), Slotted(), object()])
def test_an_unreadable_model_fails_closed(model: object) -> None:
    with pytest.raises(TypeError, match="cost model"):
        cost_model_fingerprint(model)  # type: ignore[arg-type]
