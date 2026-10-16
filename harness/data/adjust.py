"""Split and dividend adjustment, plus checks that catch unadjusted or unrecorded splits.

Raw bars plus recorded actions in, back-adjusted bars out. Adjustment is point-in-time: pass
`end` and only actions known by then are applied, because a split next year must not rescale
the prices a strategy sees today.
"""

import itertools
from collections import defaultdict
from collections.abc import Sequence
from datetime import date, datetime

from contracts.data import Bar, CorporateAction, CorporateActionKind
from harness.data.common import Issue

JUMP_RATIO_FLOOR = 0.6
SPLIT_MATCH_TOLERANCE = 0.05
MAX_SPLIT_RATIO = 10


def _by_symbol(bars: Sequence[Bar]) -> dict[str, list[Bar]]:
    grouped: dict[str, list[Bar]] = defaultdict(list)
    for bar in sorted(bars, key=lambda b: b.ts):
        grouped[bar.symbol].append(bar)
    return grouped


def adjust_bars(
    bars: Sequence[Bar], actions: Sequence[CorporateAction], *, end: datetime | None = None
) -> list[Bar]:
    """Back-adjust: bars dated before an action's ex-date are rescaled, later ones untouched.

    Splits divide prices and multiply volume by the ratio. Dividends scale prices by
    `1 - dividend / previous raw close`; volume is unchanged.
    """
    known = [a for a in actions if end is None or a.effective_at <= end]
    result: list[Bar] = []
    for symbol, series in _by_symbol(bars).items():
        mine = [a for a in known if a.symbol == symbol]
        factors: list[tuple[date, float, float]] = []
        for action in mine:
            cutoff = action.effective_at.date()
            if action.kind is CorporateActionKind.SPLIT:
                factors.append((cutoff, 1.0 / action.value, action.value))
                continue
            prior = [b for b in series if b.ts.date() < cutoff]
            if prior and prior[-1].close > action.value:
                factors.append((cutoff, 1.0 - action.value / prior[-1].close, 1.0))
        for bar in series:
            price_f = 1.0
            volume_f = 1.0
            for cutoff, pf, vf in factors:
                if bar.ts.date() < cutoff:
                    price_f *= pf
                    volume_f *= vf
            if price_f == 1.0 and volume_f == 1.0:
                result.append(bar)
                continue
            result.append(
                bar.model_copy(
                    update={
                        "open": bar.open * price_f,
                        "high": bar.high * price_f,
                        "low": bar.low * price_f,
                        "close": bar.close * price_f,
                        "volume": bar.volume * volume_f,
                    }
                )
            )
    return sorted(result, key=lambda b: (b.ts, b.symbol))


def find_unadjusted_jumps(bars: Sequence[Bar]) -> list[Issue]:
    """Flag close-to-close moves beyond the sanity band. After correct adjustment none remain
    except genuine crashes, which a human should look at anyway."""
    issues: list[Issue] = []
    for symbol, series in _by_symbol(bars).items():
        for prev, cur in itertools.pairwise(series):
            ratio = cur.close / prev.close
            if ratio < JUMP_RATIO_FLOOR or ratio > 1.0 / JUMP_RATIO_FLOOR:
                issues.append(
                    Issue("suspect_jump", symbol, cur.ts, f"close moved by factor {ratio:.3f}")
                )
    return issues


def find_unrecorded_splits(bars: Sequence[Bar], actions: Sequence[CorporateAction]) -> list[Issue]:
    """Raw prices that fall (or rise) by a near-integer factor with no split on record."""
    issues: list[Issue] = []
    splits = [a for a in actions if a.kind is CorporateActionKind.SPLIT]
    for symbol, series in _by_symbol(bars).items():
        for prev, cur in itertools.pairwise(series):
            ratio = prev.close / cur.close
            matches = any(
                abs(ratio / k - 1) < SPLIT_MATCH_TOLERANCE
                or abs(ratio * k - 1) < SPLIT_MATCH_TOLERANCE
                for k in range(2, MAX_SPLIT_RATIO + 1)
            )
            if not matches:
                continue
            recorded = any(
                a.symbol == symbol and prev.ts.date() < a.effective_at.date() <= cur.ts.date()
                for a in splits
            )
            if not recorded:
                issues.append(
                    Issue(
                        "unrecorded_split",
                        symbol,
                        cur.ts,
                        f"close ratio {ratio:.3f} looks like a split with none recorded",
                    )
                )
    return issues
