import uuid

import pytest

from contracts.registry import DuplicateTrialError, Outcome, Registry
from tests.helpers import make_trial


def test_append_then_get_round_trips(registry: Registry) -> None:
    trial = make_trial()
    registry.append(trial)
    assert registry.get(trial.id) == trial


def test_get_unknown_returns_none(registry: Registry) -> None:
    assert registry.get(uuid.uuid4()) is None


def test_count_grows_with_every_append(registry: Registry) -> None:
    assert registry.count() == 0
    for expected in (1, 2, 3):
        registry.append(make_trial())
        assert registry.count() == expected


def test_failed_trials_are_kept(registry: Registry) -> None:
    registry.append(make_trial(outcome="rejected"))
    registry.append(make_trial(outcome="error"))
    assert registry.count() == 2


def test_duplicate_id_is_rejected_and_does_not_change_count(registry: Registry) -> None:
    trial = make_trial()
    registry.append(trial)
    with pytest.raises(DuplicateTrialError):
        registry.append(trial)
    assert registry.count() == 1


def test_list_filters_by_strategy_and_keeps_insertion_order(registry: Registry) -> None:
    a1, b1, a2 = make_trial("a"), make_trial("b"), make_trial("a")
    for t in (a1, b1, a2):
        registry.append(t)
    assert [t.id for t in registry.list("a")] == [a1.id, a2.id]
    assert [t.id for t in registry.list()] == [a1.id, b1.id, a2.id]


def test_stored_trials_cannot_be_mutated(registry: Registry) -> None:
    trial = make_trial()
    registry.append(trial)
    stored = registry.get(trial.id)
    assert stored is not None
    with pytest.raises(Exception, match="frozen"):
        stored.outcome = Outcome.PASSED  # type: ignore[misc]
