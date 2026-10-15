"""Every validation threshold, in one frozen place.

Owner-approved at the Phase 1 gate. No teammate may change a value to make a case pass: a
change is a contract-style change that goes through the lead to the owner.

The values are starting points chosen from common practice, not validated numbers. The regime
table below is approximate and ends at 2025-01-01; the owner confirms the dates and defines any
later regimes before relying on the regime floor for newer data.
"""

import itertools
from datetime import UTC, datetime
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from harness.api import Regime


def _utc(year: int, month: int, day: int) -> datetime:
    return datetime(year, month, day, tzinfo=UTC)


DEFAULT_REGIMES: tuple[Regime, ...] = (
    Regime(name="low_vol_bull", start=_utc(2016, 1, 1), end=_utc(2018, 1, 26)),
    Regime(name="correction_2018", start=_utc(2018, 1, 26), end=_utc(2018, 12, 24)),
    Regime(name="late_cycle_bull", start=_utc(2018, 12, 24), end=_utc(2020, 2, 19)),
    Regime(name="covid_crash", start=_utc(2020, 2, 19), end=_utc(2020, 3, 23)),
    Regime(name="post_covid_bull", start=_utc(2020, 3, 23), end=_utc(2022, 1, 3)),
    Regime(name="bear_2022", start=_utc(2022, 1, 3), end=_utc(2022, 10, 12)),
    Regime(name="recovery_bull", start=_utc(2022, 10, 12), end=_utc(2025, 1, 1)),
)


class Thresholds(BaseModel):
    model_config = ConfigDict(frozen=True)

    # walk_forward: embargo is the label horizon, but never below this many bars.
    embargo_min_bars: int = Field(default=1, ge=1)
    # trial_adjusted
    dsr_min_probability: float = Field(default=0.95, gt=0.0, lt=1.0)
    pbo_max: float = Field(default=0.5, gt=0.0, le=1.0)
    # cost_sensitivity: re-run at this multiple of costs; profit factor must stay above the floor.
    cost_multiplier: float = Field(default=2.0, gt=1.0)
    cost_min_profit_factor: float = Field(default=1.0, gt=0.0)
    # stability: performance must hold within this fraction of the peak across neighbors.
    stability_tolerance: float = Field(default=0.20, gt=0.0, lt=1.0)
    # floors
    min_trades: int = Field(default=100, ge=1)
    min_profitable_regimes: int = Field(default=2, ge=1)
    regimes: tuple[Regime, ...] = DEFAULT_REGIMES

    @model_validator(mode="after")
    def _regimes_consistent(self) -> Self:
        ordered = sorted(self.regimes, key=lambda r: r.start)
        if any(b.start < a.end for a, b in itertools.pairwise(ordered)):
            raise ValueError("regimes must not overlap")
        if len({r.name for r in self.regimes}) != len(self.regimes):
            raise ValueError("regime names must be unique")
        if self.min_profitable_regimes > len(self.regimes):
            raise ValueError("cannot require more profitable regimes than the table defines")
        return self


DEFAULT_THRESHOLDS = Thresholds()
