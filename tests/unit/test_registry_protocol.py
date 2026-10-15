import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError

from contracts.registry import Outcome, Registry, Stage, Trial

FORBIDDEN = {
    "update",
    "delete",
    "remove",
    "pop",
    "clear",
    "upsert",
    "replace",
    "set",
    "put",
    "insert_or_replace",
    "truncate",
    "drop",
}


def make_trial(**overrides: object) -> Trial:
    base: dict[str, object] = {
        "id": uuid.uuid4(),
        "created_at": datetime(2026, 1, 5, tzinfo=UTC),
        "strategy_id": "baseline",
        "code_hash": "abc123",
        "params": {"lookback": 5},
        "capital_amount": Decimal("10000"),
        "capital_currency": "USD",
        "data_snapshot_id": "snap-1",
        "survivorship_biased": True,
        "stage_reached": Stage.LOOKAHEAD,
        "outcome": Outcome.REJECTED,
        "metrics": {"sharpe": 0.4},
    }
    base.update(overrides)
    return Trial(**base)


def test_registry_protocol_exposes_no_mutation_methods() -> None:
    public = {n for n in dir(Registry) if not n.startswith("_")}
    assert public.isdisjoint(FORBIDDEN), public & FORBIDDEN
    assert {"append", "get", "list", "count"} <= public


def test_trial_is_immutable() -> None:
    trial = make_trial()
    with pytest.raises(ValidationError):
        trial.outcome = Outcome.PASSED  # type: ignore[misc]


def test_trial_rejects_naive_created_at() -> None:
    with pytest.raises(ValidationError):
        make_trial(created_at=datetime(2026, 1, 5))


def test_trial_requires_survivorship_flag_explicitly() -> None:
    data = make_trial().model_dump()
    del data["survivorship_biased"]
    with pytest.raises(ValidationError):
        Trial(**data)


def test_stage_order_matches_validation_pipeline() -> None:
    assert [s.value for s in Stage] == [
        "lookahead",
        "walk_forward",
        "trial_adjusted",
        "cost_sensitivity",
        "stability",
        "floors",
        "paper",
        "owner_gate",
    ]


def test_trial_params_and_metrics_cannot_be_mutated() -> None:
    trial = make_trial()
    with pytest.raises(TypeError):
        trial.metrics["sharpe"] = 99.0  # type: ignore[index]
    with pytest.raises(TypeError):
        trial.params["lookback"] = 1  # type: ignore[index]


def test_trial_does_not_alias_the_callers_dicts() -> None:
    metrics = {"sharpe": 0.4}
    trial = make_trial(metrics=metrics)
    metrics["sharpe"] = 99.0
    assert trial.metrics["sharpe"] == 0.4


def test_trial_survives_pickle_and_deepcopy() -> None:
    import copy
    import pickle

    trial = make_trial()
    assert pickle.loads(pickle.dumps(trial)) == trial
    assert copy.deepcopy(trial) == trial
