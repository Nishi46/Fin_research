import sqlite3
import uuid

import pytest

from contracts.registry import DuplicateTrialError
from harness.registry.sqlite import SqliteRegistry
from tests.helpers import make_trial

INSERT = (
    "INSERT {verb} INTO trials (id, created_at, strategy_id, code_hash, params, capital_amount,"
    " capital_currency, data_snapshot_id, survivorship_biased, stage_reached, outcome, metrics)"
    " VALUES (?, 'x', 's', 'h', '{{}}', '1', 'USD', 'snap', 1, 'lookahead', 'rejected', '{{}}')"
    " {tail}"
)


def test_round_trip_preserves_every_field() -> None:
    registry = SqliteRegistry()
    trial = make_trial(params={"a": [1, 2], "b": {"c": None}}, metrics={"sharpe": -0.25})
    registry.append(trial)
    assert registry.get(trial.id) == trial


def test_trials_persist_in_a_file_across_connections(tmp_path: object) -> None:
    path = str(tmp_path / "reg.sqlite")  # type: ignore[operator]
    trial = make_trial()
    SqliteRegistry(path).append(trial)
    assert SqliteRegistry(path).get(trial.id) == trial


def test_duplicate_is_rejected_and_original_kept() -> None:
    registry = SqliteRegistry()
    trial = make_trial(metrics={"sharpe": 1.0})
    registry.append(trial)
    with pytest.raises(DuplicateTrialError):
        registry.append(make_trial(id=trial.id, metrics={"sharpe": 9.0}))
    assert registry.get(trial.id) == trial
    assert registry.count() == 1


@pytest.mark.parametrize(
    "sql",
    [
        INSERT.format(verb="OR REPLACE", tail=""),
        INSERT.format(verb="OR IGNORE", tail=""),
        INSERT.format(verb="", tail="ON CONFLICT(id) DO UPDATE SET outcome = 'passed'"),
        INSERT.format(verb="", tail="ON CONFLICT(id) DO NOTHING"),
    ],
    ids=["replace", "ignore", "upsert", "do_nothing"],
)
def test_rewriting_an_existing_id_by_raw_sql_is_blocked(sql: str) -> None:
    registry = SqliteRegistry()
    trial = make_trial()
    registry.append(trial)
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        registry.connection.execute(sql, (str(trial.id),))
    assert registry.get(trial.id) == trial


def test_update_and_delete_are_blocked() -> None:
    registry = SqliteRegistry()
    trial = make_trial()
    registry.append(trial)
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        registry.connection.execute("UPDATE trials SET outcome = 'passed'")
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        registry.connection.execute("DELETE FROM trials")
    assert registry.count() == 1
    assert registry.get(trial.id) == trial


def test_unknown_id_is_none_and_list_is_insertion_ordered() -> None:
    registry = SqliteRegistry()
    assert registry.get(uuid.uuid4()) is None
    trials = [make_trial("a"), make_trial("b"), make_trial("a")]
    for t in trials:
        registry.append(t)
    assert [t.id for t in registry.list()] == [t.id for t in trials]
    assert [t.id for t in registry.list("b")] == [trials[1].id]
