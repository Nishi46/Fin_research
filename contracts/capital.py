from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, field_validator

from contracts._time import UtcDatetime

DEFAULT_CAPITAL_USD = Decimal("10000")

CapitalSource = Literal["default", "env", "website"]


class CapitalConfig(BaseModel):
    """Account size for a backtest, forward test or run.

    Always passed explicitly; never read from a global. Results at one capital level say
    nothing about another.
    """

    model_config = ConfigDict(frozen=True)

    amount: Decimal = DEFAULT_CAPITAL_USD
    currency: str = "USD"
    effective_at: UtcDatetime
    source: CapitalSource

    @field_validator("amount")
    @classmethod
    def _positive_and_finite(cls, value: Decimal) -> Decimal:
        if not value.is_finite() or value <= 0:
            raise ValueError("capital amount must be a positive, finite number")
        return value
