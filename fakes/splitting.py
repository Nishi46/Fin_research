from collections.abc import Iterator, Sequence
from datetime import datetime

from contracts.splitting import Split


class ExpandingWindowSplitter:
    """Forward-only expanding-window splits with a purge gap before each test block.

    Training never includes data after a test block, so `embargo` has no effect here; it is
    validated and accepted to satisfy the contract (real splitters that train on both sides
    of a test block need it).
    """

    def splits(
        self, timestamps: Sequence[datetime], *, n_splits: int, purge: int, embargo: int
    ) -> Iterator[Split]:
        if purge < 0 or embargo < 0:
            raise ValueError("purge and embargo must be non-negative")
        ordered = sorted(timestamps)
        block = len(ordered) // (n_splits + 1) if n_splits >= 1 else 0
        if block < 1:
            raise ValueError("timestamps too short for the requested splits")
        for i in range(n_splits):
            test_start = (i + 1) * block
            test_end = min(test_start + block, len(ordered)) - 1
            train_end = test_start - 1 - purge
            if train_end < 0:
                raise ValueError("timestamps too short for the requested purge")
            yield Split(
                train_start=ordered[0],
                train_end=ordered[train_end],
                test_start=ordered[test_start],
                test_end=ordered[test_end],
            )
