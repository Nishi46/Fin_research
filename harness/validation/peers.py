"""Which trials a candidate's PBO is judged against, and the matrix CSCV runs on.

Trials are comparable only if they saw the same data, window, costs and capital, so each trial's
return series is filed under a group key built from exactly those inputs. The symbol list is not
part of the key (spec decision): two universes over the same window and snapshot are compared.
"""

import hashlib
import json
from datetime import datetime
from decimal import Decimal

import numpy as np
from numpy.typing import NDArray

from contracts import Registry, ReturnSeries

FREQUENCY = "daily"


def group_key(
    *,
    snapshot_id: str,
    start: datetime,
    end: datetime,
    cost_multiplier: float,
    capital_amount: Decimal,
    capital_currency: str,
) -> str:
    parts = [
        snapshot_id,
        start.isoformat(),
        end.isoformat(),
        repr(cost_multiplier),
        str(capital_amount),
        capital_currency,
        FREQUENCY,
    ]
    return hashlib.sha256(json.dumps(parts).encode("utf-8")).hexdigest()


def peer_matrix(
    registry: Registry, candidate: ReturnSeries, *, max_peers: int
) -> NDArray[np.float64]:
    """T x N matrix with the candidate in column 0 and up to `max_peers` earlier trials after it.

    The registry guarantees every series in a group shares the group's timestamps, so the columns
    line up row for row.
    """
    peers = registry.returns_for_group(
        candidate.group_key, limit=max_peers, exclude=candidate.trial_id
    )
    columns = [candidate.returns, *(p.returns for p in peers)]
    return np.column_stack([np.asarray(c, dtype=np.float64) for c in columns])
