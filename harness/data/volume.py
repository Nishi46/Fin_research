"""Volume handling. IEX-only volume is a small slice of the consolidated tape, so liquidity
caps built on it are far too tight; consolidated volume fixes that where we have it.

When we do not, we keep the IEX number (or shrink it) and label the basis. Scaling it UP to
guess consolidated volume would overstate liquidity, which is the unsafe direction, so that
is refused here: an owner-approved share estimate belongs in a reviewed change.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from enum import StrEnum

from contracts.data import Bar


class VolumeBasis(StrEnum):
    CONSOLIDATED = "consolidated"
    MIXED = "mixed"
    IEX_ONLY = "iex_only"


@dataclass(frozen=True)
class ResolvedVolume:
    bars: list[Bar]
    basis: VolumeBasis


def resolve_volume(
    iex_bars: Sequence[Bar],
    consolidated: Sequence[Bar] | None,
    *,
    iex_scale: float = 1.0,
) -> ResolvedVolume:
    if iex_scale > 1.0:
        raise ValueError("iex_scale above 1.0 would overstate liquidity")
    if iex_scale <= 0:
        raise ValueError("iex_scale must be positive")
    full: dict[tuple[str, date], float] = {
        (b.symbol, b.ts.date()): b.volume for b in (consolidated or [])
    }
    out: list[Bar] = []
    hits = 0
    for bar in iex_bars:
        key = (bar.symbol, bar.ts.date())
        if key in full:
            hits += 1
            out.append(bar.model_copy(update={"volume": full[key]}))
        else:
            out.append(bar.model_copy(update={"volume": bar.volume * iex_scale}))
    if iex_bars and hits == len(iex_bars):
        basis = VolumeBasis.CONSOLIDATED
    elif hits:
        basis = VolumeBasis.MIXED
    else:
        basis = VolumeBasis.IEX_ONLY
    return ResolvedVolume(bars=out, basis=basis)
