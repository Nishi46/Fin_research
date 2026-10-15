import math
from datetime import UTC, datetime

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import ValidationError

from contracts.intent import Intent

ASOF = datetime(2026, 1, 5, 21, 0, tzinfo=UTC)


def test_valid_intent() -> None:
    intent = Intent(asof=ASOF, weights={"SPY": 0.5, "QQQ": 0.5})
    assert intent.weights["SPY"] == 0.5


def test_empty_intent_means_all_cash() -> None:
    assert Intent(asof=ASOF, weights={}).weights == {}


@pytest.mark.parametrize("bad", [-0.01, 1.01, math.nan, math.inf, -math.inf])
def test_rejects_bad_single_weight(bad: float) -> None:
    with pytest.raises(ValidationError):
        Intent(asof=ASOF, weights={"SPY": bad})


def test_rejects_sum_above_one() -> None:
    with pytest.raises(ValidationError):
        Intent(asof=ASOF, weights={"SPY": 0.6, "QQQ": 0.6})


def test_accepts_sum_of_exactly_one_despite_float_error() -> None:
    Intent(asof=ASOF, weights={f"S{i}": 0.1 for i in range(10)})


def test_rejects_empty_symbol() -> None:
    with pytest.raises(ValidationError):
        Intent(asof=ASOF, weights={"": 0.1})


def test_rejects_naive_asof() -> None:
    with pytest.raises(ValidationError):
        Intent(asof=datetime(2026, 1, 5, 21, 0), weights={})


def test_is_immutable() -> None:
    intent = Intent(asof=ASOF, weights={})
    with pytest.raises(ValidationError):
        intent.asof = ASOF  # type: ignore[misc]


@given(st.lists(st.floats(min_value=0.0, max_value=1.0), min_size=1, max_size=8))
def test_sum_rule_matches_property(ws: list[float]) -> None:
    weights = {f"S{i}": w for i, w in enumerate(ws)}
    if sum(ws) <= 1.0 + 1e-9:
        Intent(asof=ASOF, weights=weights)
    elif sum(ws) > 1.0 + 1e-6:
        with pytest.raises(ValidationError):
            Intent(asof=ASOF, weights=weights)


def test_weights_cannot_be_mutated_after_validation() -> None:
    intent = Intent(asof=ASOF, weights={"SPY": 0.5})
    with pytest.raises(TypeError):
        intent.weights["SPY"] = 5.0  # type: ignore[index]
    with pytest.raises(TypeError):
        intent.weights["QQQ"] = -3.0  # type: ignore[index]


def test_weights_are_copied_so_the_caller_cannot_change_them_later() -> None:
    source = {"SPY": 0.5}
    intent = Intent(asof=ASOF, weights=source)
    source["SPY"] = 9.0
    assert intent.weights["SPY"] == 0.5


def test_symbols_must_be_exact_not_padded() -> None:
    with pytest.raises(ValidationError):
        Intent(asof=ASOF, weights={"AAA ": 0.1})


def test_intent_still_serializes_to_json() -> None:
    assert '"SPY":0.5' in Intent(asof=ASOF, weights={"SPY": 0.5}).model_dump_json()


def test_intent_survives_pickle_and_deepcopy() -> None:
    import copy
    import pickle

    intent = Intent(asof=ASOF, weights={"SPY": 0.5, "QQQ": 0.25})
    assert pickle.loads(pickle.dumps(intent)) == intent
    assert copy.deepcopy(intent) == intent
    assert copy.deepcopy(intent).weights is not intent.weights
