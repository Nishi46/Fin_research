import uuid
from datetime import timedelta

import pytest

from contracts.registry import (
    DuplicateTrialError,
    Outcome,
    Registry,
    ReturnSeries,
    ReturnSeriesMismatchError,
)
from tests.helpers import T0, make_trial


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


# --- per-trial return series (true PBO) ---------------------------------------------------------


def _series(
    group: str = "g1",
    *,
    days: int = 5,
    trial_id: uuid.UUID | None = None,
    start_day: int = 0,
    value: float = 0.01,
) -> ReturnSeries:
    return ReturnSeries(
        trial_id=trial_id or uuid.uuid4(),
        group_key=group,
        timestamps=tuple(T0 + timedelta(days=start_day + i) for i in range(days)),
        returns=tuple(value * (i + 1) for i in range(days)),
    )


def test_series_round_trips_within_float32_tolerance(registry: Registry) -> None:
    series = _series()
    registry.append_returns(series)
    (stored,) = registry.returns_for_group("g1", limit=10)
    assert stored.trial_id == series.trial_id
    assert stored.group_key == "g1"
    assert stored.timestamps == series.timestamps
    assert stored.returns == pytest.approx(series.returns, rel=1e-6)


def test_second_series_for_the_same_trial_is_rejected(registry: Registry) -> None:
    series = _series()
    registry.append_returns(series)
    with pytest.raises(DuplicateTrialError):
        registry.append_returns(_series(trial_id=series.trial_id))
    assert len(registry.returns_for_group("g1", limit=10)) == 1


def test_groups_are_isolated(registry: Registry) -> None:
    registry.append_returns(_series("g1"))
    registry.append_returns(_series("g2"))
    assert len(registry.returns_for_group("g1", limit=10)) == 1
    assert len(registry.returns_for_group("g2", limit=10)) == 1
    assert registry.returns_for_group("missing", limit=10) == []


def test_series_come_back_most_recent_first_and_respect_limit(registry: Registry) -> None:
    ids = [uuid.uuid4() for _ in range(4)]
    for trial_id in ids:
        registry.append_returns(_series(trial_id=trial_id))
    assert [s.trial_id for s in registry.returns_for_group("g1", limit=10)] == ids[::-1]
    assert [s.trial_id for s in registry.returns_for_group("g1", limit=2)] == ids[:1:-1]


def test_exclude_leaves_out_one_trial(registry: Registry) -> None:
    keep, drop = _series(), _series()
    registry.append_returns(keep)
    registry.append_returns(drop)
    got = registry.returns_for_group("g1", limit=10, exclude=drop.trial_id)
    assert [s.trial_id for s in got] == [keep.trial_id]


def test_series_with_different_timestamps_than_the_group_is_refused(registry: Registry) -> None:
    registry.append_returns(_series())
    with pytest.raises(ReturnSeriesMismatchError):
        registry.append_returns(_series(start_day=1))
    with pytest.raises(ReturnSeriesMismatchError):
        registry.append_returns(_series(days=4))
    assert len(registry.returns_for_group("g1", limit=10)) == 1


def test_a_refused_series_does_not_burn_the_trial_id(registry: Registry) -> None:
    registry.append_returns(_series())
    trial_id = uuid.uuid4()
    with pytest.raises(ReturnSeriesMismatchError):
        registry.append_returns(_series(start_day=1, trial_id=trial_id))
    registry.append_returns(_series("g2", trial_id=trial_id))


def test_limit_must_be_positive(registry: Registry) -> None:
    with pytest.raises(ValueError, match="limit"):
        registry.returns_for_group("g1", limit=0)


def test_series_must_be_non_empty_and_aligned() -> None:
    now = T0
    with pytest.raises(ValueError, match="same length"):
        ReturnSeries(trial_id=uuid.uuid4(), group_key="g", timestamps=(now,), returns=(0.1, 0.2))
    with pytest.raises(ValueError, match="at least one"):
        ReturnSeries(trial_id=uuid.uuid4(), group_key="g", timestamps=(), returns=())


def test_series_survive_unrelated_trial_appends(registry: Registry) -> None:
    registry.append_returns(_series())
    registry.append(make_trial())
    assert registry.count() == 1  # count() is the trial count that feeds DSR, not series count
    assert len(registry.returns_for_group("g1", limit=10)) == 1
