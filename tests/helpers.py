import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from contracts.data import Bar, DataProvider
from contracts.registry import Outcome, Stage, Trial
from harness.data.calendar import session_close_utc
from harness.data.window import ResearchWindow

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


def research_provider(
    inner: DataProvider, research_end: date | None = None, *, end: datetime | None = None
) -> ResearchWindow:
    """Wrap `inner` as `run_pipeline` requires, leaving everything the test uses visible.

    By default `research_end` is the first date whose session close is at or after the newest
    data the inner provider has (and after `end`, when given), so nothing the test serves is
    clamped away. Synthetic bars are stamped 21:00 UTC, later than the real close in summer, so
    the date can be one past the last bar's.
    """
    if research_end is None:
        newest = inner.snapshot().end
        if end is not None:
            newest = max(newest, end)
        research_end = newest.date()
        while session_close_utc(research_end) < newest:
            research_end += timedelta(days=1)
    return ResearchWindow(inner, research_end)
