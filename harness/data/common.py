from dataclasses import dataclass
from datetime import date, datetime


@dataclass(frozen=True)
class Issue:
    """One data-quality finding. `kind` is a stable machine-readable label."""

    kind: str
    symbol: str
    ts: datetime | None
    detail: str


@dataclass(frozen=True)
class SpinOff:
    """A spin-off the data layer cannot adjust for: the parent's price drops by the spun-off
    value on `ex_date`, which would look like a crash in a raw series."""

    source_symbol: str
    ex_date: date
    new_symbol: str
    new_rate: float
