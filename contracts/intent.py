import math
from collections.abc import Mapping

from pydantic import BaseModel, ConfigDict, field_validator

from contracts._frozen import FrozenFloatMap
from contracts._time import UtcDatetime

# Float addition error must not reject a legitimate "fully invested" intent.
SUM_TOLERANCE = 1e-9


class Intent(BaseModel):
    """What a strategy wants: target weights as fractions of capital. Never orders.

    Long-only: each weight in [0, 1], total at most 1. An empty mapping means all cash.
    """

    model_config = ConfigDict(frozen=True)

    asof: UtcDatetime
    weights: FrozenFloatMap

    @field_validator("weights")
    @classmethod
    def _valid_weights(cls, weights: Mapping[str, float]) -> Mapping[str, float]:
        for symbol, weight in weights.items():
            if not symbol or symbol != symbol.strip():
                raise ValueError("symbol must be non-empty with no surrounding whitespace")
            if not math.isfinite(weight) or not 0.0 <= weight <= 1.0:
                raise ValueError(f"weight for {symbol} must be finite and within [0, 1]")
        if sum(weights.values()) > 1.0 + SUM_TOLERANCE:
            raise ValueError("weights must sum to at most 1")
        return weights
