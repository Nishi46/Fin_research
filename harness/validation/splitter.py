"""Purged walk-forward splits. Expanding window, forward only; never random k-fold.

Layout for n bars and k splits: n is cut into k+1 equal blocks (any remainder goes to the
start of the first training window so every bar is used). Block 0 plus the remainder only ever
trains; test block i (i = 1..k) is scored by a model trained on everything before it.

Judgement call on `embargo`: with a forward-only training window nothing is trained after a
test block, so a classic embargo has nothing to remove. Instead the train/test gap is
`purge + embargo` bars, which is conservative: the caller passes the label horizon as `purge`
or `embargo` (the pipeline uses max(label horizon, embargo_min_bars)) and gets at least that
many bars skipped before each test block. Both are counted in bars.
"""

from collections.abc import Iterator, Sequence
from datetime import datetime
from itertools import pairwise

from contracts.splitting import Split


class PurgedWalkForwardSplitter:
    def splits(
        self, timestamps: Sequence[datetime], *, n_splits: int, purge: int, embargo: int
    ) -> Iterator[Split]:
        if purge < 0 or embargo < 0:
            raise ValueError("purge and embargo must be non-negative")
        ordered = list(timestamps)
        if any(b <= a for a, b in pairwise(ordered)):
            raise ValueError("timestamps must be strictly increasing")
        block = len(ordered) // (n_splits + 1) if n_splits >= 1 else 0
        if block < 1:
            raise ValueError("timestamps too short for the requested splits")
        remainder = len(ordered) - (n_splits + 1) * block
        gap = purge + embargo
        for i in range(n_splits):
            test_start = remainder + (i + 1) * block
            train_end = test_start - 1 - gap
            if train_end < 0:
                raise ValueError("timestamps too short for the requested purge and embargo")
            yield Split(
                train_start=ordered[0],
                train_end=ordered[train_end],
                test_start=ordered[test_start],
                test_end=ordered[test_start + block - 1],
            )
