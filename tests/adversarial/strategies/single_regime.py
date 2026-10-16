from collections.abc import Sequence

from tests.adversarial.strategies.reversal import ReversalStrategy

DEFAULT_LOOKBACK = 15


class SingleRegimeWinner(ReversalStrategy):
    """A reversal strategy; the world (not the code) makes it live off one regime."""

    def __init__(self, symbols: Sequence[str], lookback: int = DEFAULT_LOOKBACK) -> None:
        super().__init__(symbols, lookback=lookback, top_n=3, strategy_id="adv_single_regime")
