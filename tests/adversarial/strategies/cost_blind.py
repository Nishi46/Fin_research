from collections.abc import Sequence

from tests.adversarial.strategies.reversal import ReversalStrategy

DEFAULT_LOOKBACK = 10


class CostBlindReversal(ReversalStrategy):
    """Rotates into the single worst recent performer. Real but thin edge, high turnover."""

    def __init__(self, symbols: Sequence[str], lookback: int = DEFAULT_LOOKBACK) -> None:
        super().__init__(symbols, lookback=lookback, top_n=1, strategy_id="adv_cost_blind")
