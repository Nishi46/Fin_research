from contracts.capital import CapitalConfig
from contracts.costs import MarketState, Order

BPS = 10_000.0  # capital-literal-ok: basis points per unit


class ImpactCostModel:
    """Toy cost model: a flat spread/commission term plus impact that grows with order size
    relative to daily dollar volume. Ignores `capital` (the contract allows models to use it).
    """

    def __init__(self, base_bps: float = 5.0, impact_coeff: float = 10.0) -> None:
        self._base_bps = base_bps
        self._impact_coeff = impact_coeff

    def cost(self, order: Order, capital: CapitalConfig, market: MarketState) -> float:
        if market.avg_daily_dollar_volume <= 0:
            raise ValueError("average daily dollar volume must be positive")
        notional = order.shares * market.price
        participation = notional / market.avg_daily_dollar_volume
        return notional * (self._base_bps + self._impact_coeff * participation) / BPS
