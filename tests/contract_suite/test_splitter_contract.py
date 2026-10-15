from datetime import datetime, timedelta
from itertools import pairwise

import pytest

from contracts.splitting import Splitter
from tests.helpers import T0

TIMESTAMPS = [T0 + timedelta(days=i) for i in range(100)]


def bars_between(lo: datetime, hi: datetime) -> int:
    return len([t for t in TIMESTAMPS if lo < t < hi])


def test_yields_requested_number_of_splits(splitter: Splitter) -> None:
    assert len(list(splitter.splits(TIMESTAMPS, n_splits=4, purge=2, embargo=1))) == 4


def test_train_always_precedes_test_with_purge_gap(splitter: Splitter) -> None:
    for s in splitter.splits(TIMESTAMPS, n_splits=4, purge=2, embargo=1):
        assert s.train_end < s.test_start
        assert bars_between(s.train_end, s.test_start) >= 2


def test_test_windows_are_ordered_and_do_not_overlap(splitter: Splitter) -> None:
    splits = list(splitter.splits(TIMESTAMPS, n_splits=4, purge=2, embargo=1))
    for prev, nxt in pairwise(splits):
        assert prev.test_end < nxt.test_start


def test_everything_stays_inside_the_data(splitter: Splitter) -> None:
    for s in splitter.splits(TIMESTAMPS, n_splits=4, purge=2, embargo=1):
        assert s.train_start >= TIMESTAMPS[0]
        assert s.test_end <= TIMESTAMPS[-1]


def test_impossible_request_raises(splitter: Splitter) -> None:
    with pytest.raises(ValueError, match="too short"):
        list(splitter.splits(TIMESTAMPS[:5], n_splits=10, purge=2, embargo=1))


def test_negative_purge_or_embargo_raises(splitter: Splitter) -> None:
    with pytest.raises(ValueError, match="non-negative"):
        list(splitter.splits(TIMESTAMPS, n_splits=4, purge=-1, embargo=0))
