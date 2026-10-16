"""Parameter-stability stage: performance must hold across neighbouring parameter values.

The caller supplies the parameter points (the candidate's own plus its neighbours), a factory
that builds a strategy from a point, and `run`, which backtests a strategy. Each point is
scored by annualized Sharpe (`harness.metrics.core.sharpe`).

Judgement call: the rule is "every point scores at least (1 - stability_tolerance) of the best
point", and the best point must be positive. That is strict by design: a result that only works
at one value fails, which is the overfit signature this stage exists to catch. It also fails a
plateau with one weak edge, so the owner should choose the neighbour set deliberately.
"""

from collections.abc import Callable, Mapping, Sequence

from contracts import Stage
from harness.api import BacktestResult, StageResult
from harness.metrics.core import sharpe
from harness.thresholds import DEFAULT_THRESHOLDS, Thresholds

MIN_POINTS = 3
_FLOAT_DIGITS = 12  # keeps 0.5 * 1.1 equal to 0.55 so neighbours are stable and comparable


def neighbour_points(
    params: Mapping[str, int | float], fractions: Sequence[float]
) -> list[dict[str, int | float]]:
    """Points that differ from `params` in exactly one numeric parameter.

    Each numeric, non-bool parameter p becomes p * (1 +/- f) for every fraction f, with the other
    parameters fixed. Integers are rounded and forced at least one step away from p. Values that
    are not positive, and duplicates, are dropped. The candidate itself is not included.
    """
    points: list[dict[str, int | float]] = []
    for key, value in params.items():
        if isinstance(value, bool) or not isinstance(value, int | float) or value <= 0:
            continue
        seen: set[int | float] = set()
        for fraction in fractions:
            for sign in (-1, 1):
                moved = value * (1.0 + sign * fraction)
                candidate: int | float
                if isinstance(value, int):
                    candidate = round(moved)
                    if candidate == value:
                        candidate = value + sign
                else:
                    candidate = round(moved, _FLOAT_DIGITS)
                if candidate <= 0 or candidate == value or candidate in seen:
                    continue
                seen.add(candidate)
                points.append({**params, key: candidate})
    return points


def stability_stage[P, S](
    points: Sequence[P],
    build: Callable[[P], S],
    run: Callable[[S], BacktestResult],
    thresholds: Thresholds = DEFAULT_THRESHOLDS,
) -> StageResult:
    def result(passed: bool, reason: str, metrics: Mapping[str, float]) -> StageResult:
        return StageResult(stage=Stage.STABILITY, passed=passed, reason=reason, metrics=metrics)

    if len(points) < MIN_POINTS:
        return result(
            False,
            f"{len(points)} parameter points; at least {MIN_POINTS} (candidate plus "
            "neighbours) are needed to judge stability",
            {"n_points": float(len(points))},
        )

    scores = [sharpe(run(build(p)).daily_returns) for p in points]
    peak = max(scores)
    lowest = min(scores)
    metrics: dict[str, float] = {
        "n_points": float(len(points)),
        "peak_sharpe": peak,
        "min_sharpe": lowest,
    }
    if peak <= 0.0:
        return result(False, f"best Sharpe {peak:.2f} across neighbours is not positive", metrics)

    ratio = lowest / peak
    metrics["min_over_peak"] = ratio
    floor = 1.0 - thresholds.stability_tolerance
    if ratio < floor:
        return result(
            False,
            f"worst neighbour holds {ratio:.0%} of the peak Sharpe; at least {floor:.0%} required",
            metrics,
        )
    return result(True, f"every neighbour holds {ratio:.0%} of the peak Sharpe", metrics)
