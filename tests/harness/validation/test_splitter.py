from datetime import datetime, timedelta
from itertools import pairwise

import pytest
from hypothesis import given
from hypothesis import strategies as st

from harness.validation.splitter import PurgedWalkForwardSplitter
from tests.helpers import T0


def stamps(n: int) -> list[datetime]:
    return [T0 + timedelta(days=i) for i in range(n)]


@given(
    n=st.integers(min_value=20, max_value=400),
    n_splits=st.integers(min_value=1, max_value=8),
    purge=st.integers(min_value=0, max_value=6),
    embargo=st.integers(min_value=0, max_value=6),
)
def test_split_properties(n: int, n_splits: int, purge: int, embargo: int) -> None:
    ts = stamps(n)
    index = {t: i for i, t in enumerate(ts)}
    gen = PurgedWalkForwardSplitter().splits(ts, n_splits=n_splits, purge=purge, embargo=embargo)
    block = n // (n_splits + 1)
    first_train_end = (n - (n_splits + 1) * block) + block - 1 - (purge + embargo)
    if first_train_end < 0:
        with pytest.raises(ValueError, match="too short"):
            list(gen)
        return
    splits = list(gen)
    assert len(splits) == n_splits
    for s in splits:
        tr0 = index[s.train_start]
        tr1 = index[s.train_end]
        te0 = index[s.test_start]
        te1 = index[s.test_end]
        assert tr0 == 0
        assert tr0 <= tr1  # train is non-empty
        assert tr1 < te0  # train ends before test
        assert te0 - tr1 - 1 >= purge + embargo  # purge and embargo bars are skipped
        assert te0 <= te1 < n
    for prev, nxt in pairwise(splits):
        assert prev.test_end < nxt.test_start  # test windows never overlap
        assert prev.train_end < nxt.train_end  # walks forward


def test_uses_all_the_data_and_test_blocks_are_equal_length() -> None:
    ts = stamps(103)
    splits = list(PurgedWalkForwardSplitter().splits(ts, n_splits=4, purge=1, embargo=1))
    assert splits[-1].test_end == ts[-1]
    assert len({(s.test_end - s.test_start).days for s in splits}) == 1


def test_known_layout() -> None:
    ts = stamps(100)  # 5 blocks of 20, test blocks are blocks 1..4
    s = list(PurgedWalkForwardSplitter().splits(ts, n_splits=4, purge=2, embargo=3))
    assert s[0].test_start == ts[20]
    assert s[0].test_end == ts[39]
    assert s[0].train_end == ts[14]  # 20 - 1 - (2 + 3)
    assert s[3].test_end == ts[99]


def test_unsorted_or_duplicate_timestamps_are_rejected() -> None:
    ts = stamps(50)
    with pytest.raises(ValueError, match="strictly increasing"):
        list(PurgedWalkForwardSplitter().splits(ts[::-1], n_splits=3, purge=0, embargo=0))
    with pytest.raises(ValueError, match="strictly increasing"):
        list(PurgedWalkForwardSplitter().splits([*ts, ts[-1]], n_splits=3, purge=0, embargo=0))


def test_gap_larger_than_first_block_is_too_short() -> None:
    with pytest.raises(ValueError, match="too short"):
        list(PurgedWalkForwardSplitter().splits(stamps(50), n_splits=4, purge=9, embargo=9))


def test_there_is_no_random_component() -> None:
    ts = stamps(120)
    a = list(PurgedWalkForwardSplitter().splits(ts, n_splits=5, purge=2, embargo=2))
    b = list(PurgedWalkForwardSplitter().splits(ts, n_splits=5, purge=2, embargo=2))
    assert a == b
