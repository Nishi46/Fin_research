from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from contracts._time import UtcDatetime
from contracts.capital import CapitalConfig


class Order(BaseModel):
    """A sized order. Produced by the risk layer, never by a strategy."""

    model_config = ConfigDict(frozen=True)

    symbol: str
    side: Literal["buy", "sell"]
    shares: float = Field(gt=0)


class MarketState(BaseModel):
    model_config = ConfigDict(frozen=True)

    symbol: str
    asof: UtcDatetime
    price: float = Field(gt=0)
    avg_daily_dollar_volume: float = Field(ge=0)
    spread_bps: float | None = Field(default=None, ge=0)


class CostModel(Protocol):
    """Total cost of an order in USD (spread, commission, slippage, impact).

    Depends on capital and order size: the same strategy costs more at larger capital.
    """

    def cost(self, order: Order, capital: CapitalConfig, market: MarketState) -> float: ...
