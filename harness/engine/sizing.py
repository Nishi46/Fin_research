"""Default backtest sizer: target weights to share counts at the given capital.

This is NOT the risk layer (build step 3). It exists so the backtester runs; the real sizing
layer is injected later through the same `Sizer` protocol so backtest and live share one path.

Capital drives the result: a position worth less than one share (or below the minimum notional)
is dropped, and positions are capped to a share of average daily dollar volume. The same
signals therefore give different position counts and costs at different capital levels.
"""

import math
from collections.abc import Mapping

from contracts import CapitalConfig, Intent, MarketState
from harness.thresholds import DEFAULT_THRESHOLDS

FRACTIONAL_PLACES = 6


class DefaultSizer:
    def __init__(
        self,
        max_adv_participation: float | None = None,
        min_position_usd: float = 1.0,
        fractional: bool = False,
    ) -> None:
        if max_adv_participation is None:
            max_adv_participation = DEFAULT_THRESHOLDS.max_adv_participation
        if not 0.0 < max_adv_participation <= 1.0:
            raise ValueError("max_adv_participation must be in (0, 1]")
        if not math.isfinite(min_position_usd) or min_position_usd < 0:
            raise ValueError("min_position_usd must be finite and non-negative")
        self._cap = max_adv_participation
        self._min_usd = min_position_usd
        self._fractional = fractional

    @property
    def max_adv_participation(self) -> float:
        return self._cap

    def target_shares(
        self, intent: Intent, capital: CapitalConfig, market: Mapping[str, MarketState]
    ) -> Mapping[str, float]:
        account = float(capital.amount)
        targets: dict[str, float] = {}
        for symbol, weight in intent.weights.items():
            state = market.get(symbol)
            if state is None:
                raise ValueError(f"no market state for {symbol}")
            liquidity_limit = self._cap * state.avg_daily_dollar_volume
            notional = min(weight * account, liquidity_limit)
            shares = self._round(notional / state.price)
            targets[symbol] = shares if shares * state.price >= self._min_usd else 0.0
        return targets

    def _round(self, shares: float) -> float:
        if self._fractional:
            factor = 10.0**FRACTIONAL_PLACES
            return math.floor(shares * factor) / factor
        return float(math.floor(shares))
