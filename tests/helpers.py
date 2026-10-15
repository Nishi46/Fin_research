import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from contracts.data import Bar
from contracts.registry import Outcome, Stage, Trial

T0 = datetime(2026, 1, 5, 21, 0, tzinfo=UTC)


def make_bar(symbol: str, day: int, close: float = 100.0) -> Bar:
    return Bar(
        symbol=symbol,
        ts=T0 + timedelta(days=day),
        open=close,
        high=close,
        low=close,
        close=close,
        volume=1_000_000.0,
    )


def make_trial(strategy_id: str = "baseline", **overrides: object) -> Trial:
    base: dict[str, object] = {
        "id": uuid.uuid4(),
        "created_at": T0,
        "strategy_id": strategy_id,
        "code_hash": "abc123",
        "params": {"lookback": 5},
        "capital_amount": Decimal("10000"),
        "capital_currency": "USD",
        "data_snapshot_id": "snap-1",
        "survivorship_biased": True,
        "stage_reached": Stage.LOOKAHEAD,
        "outcome": Outcome.REJECTED,
        "metrics": {"sharpe": 0.1},
    }
    base.update(overrides)
    return Trial.model_validate(base)
