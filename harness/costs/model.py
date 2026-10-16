"""Transaction cost model: spread, commission, slippage and size-dependent impact.

Cost of an order in USD = notional * (half spread + slippage + impact) + commission, where
impact_bps = impact_bps_at_full_adv * sqrt(order notional / average daily dollar volume).

The square-root impact law makes cost per dollar grow with order size, so total cost grows
faster than linearly: a larger order never costs proportionally less. Capital affects cost only
through order size (the same weights are bigger orders at bigger capital), which is where the
liquidity effects come from; `capital` is accepted because the contract passes it.

Every default comes from the frozen `Thresholds` (`harness.thresholds`); pass a `Thresholds`
or an explicit argument to override. They are conservative starting points for liquid US large
caps and ETFs, not fitted numbers.
"""

import math

from contracts.capital import CapitalConfig
from contracts.costs import CostModel, MarketState, Order
from harness.thresholds import DEFAULT_THRESHOLDS, Thresholds

BPS = 10_000.0  # capital-literal-ok: basis points per unit


class LiquidityCapExceededError(ValueError):
    """The order is a larger share of average daily dollar volume than the cap allows."""


def _non_negative(name: str, value: float) -> float:
    if not math.isfinite(value) or value < 0:
        raise ValueError(f"{name} must be finite and non-negative")
    return value


class RealCostModel:
    def __init__(
        self,
        thresholds: Thresholds = DEFAULT_THRESHOLDS,
        *,
        default_spread_bps: float | None = None,
        slippage_bps: float | None = None,
        commission_per_share: float | None = None,
        impact_bps_at_full_adv: float | None = None,
        max_adv_participation: float | None = None,
    ) -> None:
        def pick(explicit: float | None, configured: float) -> float:
            return configured if explicit is None else explicit

        self._default_spread_bps = _non_negative(
            "default_spread_bps", pick(default_spread_bps, thresholds.cost_default_spread_bps)
        )
        self._slippage_bps = _non_negative(
            "slippage_bps", pick(slippage_bps, thresholds.cost_slippage_bps)
        )
        self._commission_per_share = _non_negative(
            "commission_per_share",
            pick(commission_per_share, thresholds.cost_commission_per_share),
        )
        self._impact_bps = _non_negative(
            "impact_bps_at_full_adv",
            pick(impact_bps_at_full_adv, thresholds.cost_impact_bps_at_full_adv),
        )
        cap = pick(max_adv_participation, thresholds.max_adv_participation)
        if not 0.0 < cap <= 1.0:
            raise ValueError("max_adv_participation must be in (0, 1]")
        self._cap = cap

    @property
    def default_spread_bps(self) -> float:
        return self._default_spread_bps

    @property
    def slippage_bps(self) -> float:
        return self._slippage_bps

    @property
    def commission_per_share(self) -> float:
        return self._commission_per_share

    @property
    def impact_bps_at_full_adv(self) -> float:
        return self._impact_bps

    @property
    def max_adv_participation(self) -> float:
        return self._cap

    def max_shares(self, market: MarketState) -> float:
        """Largest order, in shares, the liquidity cap allows in this market."""
        self._require_volume(market)
        return self._cap * market.avg_daily_dollar_volume / market.price

    @staticmethod
    def _require_volume(market: MarketState) -> None:
        if market.avg_daily_dollar_volume <= 0:
            raise ValueError("average daily dollar volume must be positive")

    def cost(self, order: Order, capital: CapitalConfig, market: MarketState) -> float:
        self._require_volume(market)
        notional = order.shares * market.price
        participation = notional / market.avg_daily_dollar_volume
        if participation > self._cap * (1.0 + 1e-9):
            raise LiquidityCapExceededError(
                f"order is {participation:.2%} of average daily dollar volume, "
                f"above the {self._cap:.2%} liquidity cap"
            )
        spread_bps = (
            market.spread_bps if market.spread_bps is not None else self._default_spread_bps
        )
        impact_bps = self._impact_bps * math.sqrt(participation)
        proportional_bps = spread_bps / 2.0 + self._slippage_bps + impact_bps
        return notional * proportional_bps / BPS + order.shares * self._commission_per_share


class ScaledCostModel:
    """Wraps a cost model and multiplies its cost. Supports the cost-doubling run mode."""

    def __init__(self, inner: CostModel, multiplier: float) -> None:
        if not math.isfinite(multiplier) or multiplier < 1.0:
            raise ValueError("cost multiplier must be at least 1")
        self._inner = inner
        self._multiplier = multiplier

    def cost(self, order: Order, capital: CapitalConfig, market: MarketState) -> float:
        return self._inner.cost(order, capital, market) * self._multiplier
