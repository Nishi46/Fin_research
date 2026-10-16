"""SQLite `Registry` on the frozen append-only DDL.

Append-only is enforced by the database triggers in the DDL, not by this class: a raw
`UPDATE`, `DELETE`, `INSERT OR REPLACE` or upsert against `trials` is aborted by SQLite itself.
Not defended: dropping the triggers or the file by whoever owns the database file.
"""

import json
import sqlite3
import uuid
from collections.abc import Sequence
from decimal import Decimal

from contracts.registry import DuplicateTrialError, Outcome, Stage, Trial
from harness.registry._db import connect, from_text, to_text

_COLUMNS = (
    "id, created_at, strategy_id, code_hash, params, capital_amount, capital_currency,"
    " data_snapshot_id, survivorship_biased, stage_reached, outcome, metrics"
)


class SqliteRegistry:
    def __init__(self, database: str = ":memory:") -> None:
        self._conn = connect(database)

    @property
    def connection(self) -> sqlite3.Connection:
        """The raw connection. Exposed so tests can prove that raw rewrites are blocked."""
        return self._conn

    def append(self, trial: Trial) -> None:
        row = (
            str(trial.id),
            to_text(trial.created_at),
            trial.strategy_id,
            trial.code_hash,
            json.dumps(dict(trial.params)),
            str(trial.capital_amount),
            trial.capital_currency,
            trial.data_snapshot_id,
            int(trial.survivorship_biased),
            trial.stage_reached.value,
            trial.outcome.value,
            json.dumps(dict(trial.metrics)),
        )
        try:
            self._conn.execute(
                f"INSERT INTO trials ({_COLUMNS}) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", row
            )
        except sqlite3.IntegrityError as err:
            raise DuplicateTrialError(str(trial.id)) from err

    def get(self, trial_id: uuid.UUID) -> Trial | None:
        row = self._conn.execute(
            f"SELECT {_COLUMNS} FROM trials WHERE id = ?", (str(trial_id),)
        ).fetchone()
        return None if row is None else _to_trial(row)

    def list(self, strategy_id: str | None = None) -> Sequence[Trial]:
        if strategy_id is None:
            rows = self._conn.execute(f"SELECT {_COLUMNS} FROM trials ORDER BY rowid")
        else:
            rows = self._conn.execute(
                f"SELECT {_COLUMNS} FROM trials WHERE strategy_id = ? ORDER BY rowid",
                (strategy_id,),
            )
        return [_to_trial(row) for row in rows]

    def count(self) -> int:
        row = self._conn.execute("SELECT COUNT(*) FROM trials").fetchone()
        return int(row[0])


def _to_trial(row: tuple[object, ...]) -> Trial:
    return Trial(
        id=uuid.UUID(str(row[0])),
        created_at=from_text(str(row[1])),
        strategy_id=str(row[2]),
        code_hash=str(row[3]),
        params=json.loads(str(row[4])),
        capital_amount=Decimal(str(row[5])),
        capital_currency=str(row[6]),
        data_snapshot_id=str(row[7]),
        survivorship_biased=bool(row[8]),
        stage_reached=Stage(str(row[9])),
        outcome=Outcome(str(row[10])),
        metrics=json.loads(str(row[11])),
    )
