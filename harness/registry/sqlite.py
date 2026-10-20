"""SQLite `Registry` on the frozen append-only DDL.

Append-only is enforced by the database triggers in the DDL, not by this class: a raw
`UPDATE`, `DELETE`, `INSERT OR REPLACE` or upsert against `trials` is aborted by SQLite itself.
Not defended: dropping the triggers or the file by whoever owns the database file.
"""

import json
import sqlite3
import uuid
import zlib
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import numpy as np

from contracts.registry import (
    DuplicateTrialError,
    Outcome,
    ReturnSeries,
    ReturnSeriesMismatchError,
    Stage,
    Trial,
)
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

    def append_returns(self, series: ReturnSeries) -> None:
        payload = _encode_series(series)
        n = len(series.returns)
        # IMMEDIATE so the "same timestamps as the group's first series" check and the insert
        # cannot interleave with another writer.
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            first = self._conn.execute(
                "SELECT payload, n FROM trial_returns WHERE group_key = ? ORDER BY rowid LIMIT 1",
                (series.group_key,),
            ).fetchone()
            if first is not None and _timestamp_bytes(first[0], int(first[1])) != _timestamp_bytes(
                payload, n
            ):
                raise ReturnSeriesMismatchError(series.group_key)
            self._conn.execute(
                "INSERT INTO trial_returns (trial_id, group_key, n, payload, created_at)"
                " VALUES (?,?,?,?,?)",
                (
                    str(series.trial_id),
                    series.group_key,
                    n,
                    payload,
                    to_text(datetime.now(UTC)),
                ),
            )
        except sqlite3.IntegrityError as err:
            self._conn.execute("ROLLBACK")
            raise DuplicateTrialError(str(series.trial_id)) from err
        except BaseException:
            self._conn.execute("ROLLBACK")
            raise
        self._conn.execute("COMMIT")

    def returns_for_group(
        self, group_key: str, *, limit: int, exclude: uuid.UUID | None = None
    ) -> Sequence[ReturnSeries]:
        if limit < 1:
            raise ValueError("limit must be at least 1")
        rows = self._conn.execute(
            "SELECT trial_id, payload, n FROM trial_returns"
            " WHERE group_key = ? AND trial_id != ? ORDER BY rowid DESC LIMIT ?",
            (group_key, str(exclude) if exclude is not None else "", limit),
        )
        return [
            _decode_series(uuid.UUID(str(trial_id)), group_key, payload, int(n))
            for trial_id, payload, n in rows
        ]


_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_MICROSECOND = timedelta(microseconds=1)
_TS_DTYPE = np.dtype("<i8")
_RET_DTYPE = np.dtype("<f4")


def _encode_series(series: ReturnSeries) -> bytes:
    micros = np.array([(t - _EPOCH) // _MICROSECOND for t in series.timestamps], dtype=_TS_DTYPE)
    # Deltas, not absolute values: daily gaps repeat, so zlib squeezes them to almost nothing.
    stamps = np.diff(micros, prepend=0)
    returns = np.asarray(series.returns, dtype=_RET_DTYPE)
    return zlib.compress(stamps.tobytes() + returns.tobytes())


def _timestamp_bytes(payload: bytes, n: int) -> bytes:
    return zlib.decompress(payload)[: n * _TS_DTYPE.itemsize]


def _decode_series(trial_id: uuid.UUID, group_key: str, payload: bytes, n: int) -> ReturnSeries:
    raw = zlib.decompress(payload)
    split = n * _TS_DTYPE.itemsize
    stamps = np.cumsum(np.frombuffer(raw[:split], dtype=_TS_DTYPE))
    returns = np.frombuffer(raw[split:], dtype=_RET_DTYPE)
    return ReturnSeries(
        trial_id=trial_id,
        group_key=group_key,
        timestamps=tuple(_EPOCH + int(us) * _MICROSECOND for us in stamps),
        returns=tuple(float(r) for r in returns),
    )


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
