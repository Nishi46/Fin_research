"""Which trials a candidate's PBO is judged against, and the matrix CSCV runs on.

Trials are comparable only if they saw the same data, window, costs and capital, so each trial's
return series is filed under a group key built from exactly those inputs. The symbol list is not
part of the key (spec decision): two universes over the same window and snapshot are compared.
"""

import hashlib
import json
from collections.abc import Mapping
from dataclasses import asdict, is_dataclass
from datetime import UTC, datetime
from decimal import Decimal

import numpy as np
from numpy.typing import NDArray
from pydantic import BaseModel

from contracts import CostModel, Registry, ReturnSeries
from harness.costs.model import RealCostModel

FREQUENCY = "daily"


def _describe(value: object, path: str) -> object:
    """A canonical JSON-able description of a configuration value; TypeError if unreadable."""
    if value is None or isinstance(value, bool | int | str):
        return value
    if isinstance(value, float):
        return {"float": repr(value)}  # repr round-trips, and 2.0 stays distinct from 2
    if isinstance(value, Decimal):
        return {"decimal": format(value.normalize(), "f")}
    if isinstance(value, Mapping):
        return {str(k): _describe(v, f"{path}.{k}") for k, v in sorted(value.items(), key=str)}
    if isinstance(value, list | tuple):
        return [_describe(v, f"{path}[{i}]") for i, v in enumerate(value)]
    if isinstance(value, BaseModel):
        return _describe_object(value, value.model_dump(), path)
    if is_dataclass(value) and not isinstance(value, type):
        return _describe_object(value, asdict(value), path)
    try:
        state = vars(value)
    except TypeError:
        raise TypeError(
            f"cost model configuration at {path} ({type(value).__qualname__}) cannot be read: "
            "it has no instance state to fingerprint"
        ) from None
    return _describe_object(value, state, path)


def _describe_object(value: object, state: Mapping[str, object], path: str) -> object:
    cls = type(value)
    return {
        "class": f"{cls.__module__}.{cls.__qualname__}",
        "config": {k: _describe(v, f"{path}.{k}") for k, v in sorted(state.items())},
    }


def cost_model_fingerprint(model: CostModel | None) -> str:
    """Stable hash of a cost model's class and configuration, wrapped models included.

    `None` is the model `run_backtest` builds when none is given (`RealCostModel()`, whose values
    come from the frozen default Thresholds), so `None` and an equal explicit default agree. A
    model whose configuration cannot be read raises TypeError rather than getting a constant:
    a constant would put differently-costed trials in one PBO group.
    """
    effective = RealCostModel() if model is None else model
    text = json.dumps(_describe(effective, "model"), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def group_key(
    *,
    snapshot_id: str,
    start: datetime,
    end: datetime,
    cost_multiplier: float,
    capital_amount: Decimal,
    capital_currency: str,
    cost_fingerprint: str,
) -> str:
    parts = [
        snapshot_id,
        start.astimezone(UTC).isoformat(),
        end.astimezone(UTC).isoformat(),
        repr(cost_multiplier),
        cost_fingerprint,
        format(capital_amount.normalize(), "f"),  # 10000, 10000.00 and 1E+4 are one amount
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
    # Storage keeps float32, so the candidate goes through float32 too: a re-submission of a stored
    # peer is then an exact tie, and recomputing PBO later gives the same matrix.
    own = np.asarray(candidate.returns, dtype=np.float32).astype(np.float64)
    columns = [own, *(np.asarray(p.returns, dtype=np.float64) for p in peers)]
    return np.column_stack(columns)
