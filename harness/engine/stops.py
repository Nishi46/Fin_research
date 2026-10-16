"""Stop and take-profit fills for long positions on daily bars.

A stop is a trigger, not a promise of its price. If a bar opens through the stop (an overnight
gap), the position exits at the open, the next available price, never at the stop price. Only
a stop crossed during the session fills at the stop price. Within one bar the stop is assumed
to trigger before the target, which is the pessimistic order.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

ExitReason = Literal["stop", "gap_stop", "take_profit", "gap_take_profit"]


class StopRule(BaseModel):
    """Distances from the average entry price, as fractions of it."""

    model_config = ConfigDict(frozen=True)

    stop_loss_frac: float | None = Field(default=None, gt=0.0, lt=1.0)
    take_profit_frac: float | None = Field(default=None, gt=0.0)


@dataclass(frozen=True)
class ExitFill:
    price: float
    reason: ExitReason


def long_exit_fill(
    entry_price: float, rule: StopRule, *, open_: float, high: float, low: float
) -> ExitFill | None:
    stop = entry_price * (1.0 - rule.stop_loss_frac) if rule.stop_loss_frac is not None else None
    target = (
        entry_price * (1.0 + rule.take_profit_frac) if rule.take_profit_frac is not None else None
    )
    # The open comes first in time, so gaps are resolved before anything intrabar.
    if stop is not None and open_ <= stop:
        return ExitFill(price=open_, reason="gap_stop")
    if target is not None and open_ >= target:
        return ExitFill(price=open_, reason="gap_take_profit")
    if stop is not None and low <= stop:
        return ExitFill(price=stop, reason="stop")
    if target is not None and high >= target:
        return ExitFill(price=target, reason="take_profit")
    return None


def crosses_event(entry_ts: datetime, exit_ts: datetime, events: Iterable[datetime]) -> bool:
    """True if the position was held while an event (for example earnings) was released.

    Held means entry strictly before the event and exit at or after it. Such trades can gap
    through any stop, so the backtester flags them rather than trusting their fills.
    """
    return any(entry_ts < event <= exit_ts for event in events)
