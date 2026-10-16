from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class Issue:
    """One data-quality finding. `kind` is a stable machine-readable label."""

    kind: str
    symbol: str
    ts: datetime | None
    detail: str
