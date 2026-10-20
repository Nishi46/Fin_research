"""The validation pipeline: fixed stage order, fail closed, one Trial per call.

Order, never skipped or reordered: LOOKAHEAD, WALK_FORWARD, TRIAL_ADJUSTED, COST_SENSITIVITY,
STABILITY, FLOORS. The first failing stage ends the run (outcome REJECTED). A stage that cannot
be evaluated fails rather than being skipped.

Exactly one `Trial` is appended to the registry per call, whatever happens: a pass, a rejection
at any stage, or an unexpected exception (outcome ERROR, `stage_reached` is the stage that
raised). The append is in a `finally`, so no path skips it. If the append itself fails, that
exception propagates, because a silent loss of a trial would corrupt the trial count.

`run_pipeline` is the only public entry point and takes no thresholds: a caller that could pass
thresholds could weaken the grader. Tests drive `_run_pipeline`, which does.

Notes the owner should know:
* The base backtest runs once, before the lookahead stage, because the lookahead stage needs the
  trading calendar and every trial needs a `sharpe_per_period`. It is computation, not a stage.
  If the strategy raises `LookaheadError` during it, that is a LOOKAHEAD rejection.
* Regimes are derived at the floors stage from the benchmark's bars in the provider up to `end`
  (`harness.data.regimes.derive_regimes`); no usable benchmark bars fails FLOORS, never skips it.
* PBO is computed at stage 3 by CSCV across TRIALS: the candidate's net daily returns are stored
  in the registry under a group key (same data snapshot, window, cost multiplier and capital) and
  compared with up to `pbo_max_peers` earlier trials of that group. Once the group has
  `pbo_min_trials` trials it gates (`harness.validation.peers`); below that it is recorded as not
  gating. Every trial that reaches stage 3 stores a series, including ones rejected later, so the
  field is the real N. A trial rejected before stage 3 stores none. A series whose timestamps
  differ from the group's is refused: PBO is then not computed and the reason is in the stage
  text. Re-submitted identical code is a new trial and counts as an extra peer (owner decision).
  Metrics: `trial_adjusted.pbo`, `.pbo_computed`, `.pbo_gating`, `.pbo_group_size`.
* The old PBO over the candidate's derived neighbours is kept as information only, because
  near-identical neighbours measure which neighbour wins, not overfitting:
  `trial_adjusted.pbo_neighbours` and `.pbo_neighbours_computed`. Each configuration is
  backtested exactly once, after `build(params)` is shown to reproduce the submitted strategy,
  and stability reuses the results.
  A missing `build`, a non-reproducing `build` and too few configurations fail STABILITY; an
  exception while building or running a neighbour is logged as ERROR at STABILITY.
* `sharpe_per_period` is always recorded. When no return series exists (the base run failed) it
  is 0.0 with `sharpe_per_period_missing = 1.0`, and the DSR variance estimate ignores those.
"""

import hashlib
import inspect
import logging
import math
import sys
import uuid
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

import numpy as np
from numpy.typing import NDArray
from pydantic import BaseModel, ConfigDict

from contracts import (
    CapitalConfig,
    CostModel,
    DataProvider,
    LookaheadError,
    Outcome,
    Registry,
    ReturnSeries,
    ReturnSeriesMismatchError,
    Stage,
    Strategy,
    Trial,
)
from harness.api import BacktestResult, Regime, StageResult
from harness.data.regimes import derive_regimes
from harness.engine.runner import detect_code_version, run_backtest
from harness.thresholds import DEFAULT_THRESHOLDS, Thresholds
from harness.validation.cost_sensitivity import cost_sensitivity_stage
from harness.validation.dsr import (
    SHARPE_METRIC,
    SHARPE_MISSING_FLAG,
    sharpe_moments,
    trial_adjusted_stage,
)
from harness.validation.floors import floors_stage
from harness.validation.lookahead import check_lookahead
from harness.validation.peers import group_key, peer_matrix
from harness.validation.stability import MIN_POINTS, neighbour_points, stability_stage
from harness.validation.walk_forward import walk_forward_stage

logger = logging.getLogger(__name__)

# Postgres JSON columns reject Infinity, so infinities are stored as this finite constant plus a
# `<key>_capped` flag. It is far above any real ratio this harness produces.
INFINITY_CAP = 1e12
MIN_BENCHMARK_BARS = 2
_LOOKAHEAD_WARMUP_FRACTION = 0.10  # skip the first tenth, where there is little history to use


class PipelineResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    stages: tuple[StageResult, ...]
    stage_reached: Stage
    outcome: Outcome
    trial: Trial


def json_safe_metrics(metrics: Mapping[str, float]) -> dict[str, float]:
    """Cap infinities (flagging them); raise on NaN, which is an error, not a value."""
    clean: dict[str, float] = {}
    for key, value in metrics.items():
        if math.isnan(value):
            raise ValueError(f"metric {key!r} is NaN")
        if math.isinf(value):
            clean[key] = math.copysign(INFINITY_CAP, value)
            clean[f"{key}_capped"] = 1.0
        else:
            clean[key] = value
    return clean


def _prefixed(stage: Stage, metrics: Mapping[str, float]) -> dict[str, float]:
    """Prefix a stage's metrics; a NaN is flagged as `<key>_nan`, never stored, then raised."""
    out: dict[str, float] = {}
    nan_keys: list[str] = []
    for key, value in metrics.items():
        if math.isnan(value):
            nan_keys.append(key)
            out[f"{stage.value}.{key}_nan"] = 1.0
    present = {k: v for k, v in metrics.items() if k not in nan_keys}
    out.update({f"{stage.value}.{k}": v for k, v in json_safe_metrics(present).items()})
    if nan_keys:
        raise _NanMetricError(out, nan_keys)
    return out


class _NanMetricError(ValueError):
    def __init__(self, partial: dict[str, float], keys: list[str]) -> None:
        super().__init__(f"NaN metric(s): {', '.join(keys)}")
        self.partial = partial


def _strategy_id(strategy: Strategy) -> str:
    cls = type(strategy)
    return f"{cls.__module__}.{cls.__qualname__}"


def _code_hash(strategy: Strategy) -> str:
    cls = type(strategy)
    try:
        path = inspect.getsourcefile(sys.modules[cls.__module__])
        if path is None:
            raise OSError("no source file")
        with open(path, "rb") as handle:
            return hashlib.sha256(handle.read()).hexdigest()
    except (OSError, TypeError, KeyError):
        return f"unavailable:{cls.__module__}.{cls.__qualname__}"


def _check_times(timestamps: Sequence[datetime], count: int) -> list[datetime]:
    """Evenly spaced, deterministic, each with at least one later trading timestamp."""
    usable = len(timestamps) - 1
    if usable < 1:
        return []
    first = min(int(usable * _LOOKAHEAD_WARMUP_FRACTION), usable - 1)
    indices = np.unique(np.linspace(first, usable - 1, num=min(count, usable - first)).round())
    return [timestamps[int(i)] for i in indices]


def _derive_regimes(
    provider: DataProvider, end: datetime, thresholds: Thresholds
) -> tuple[Regime, ...] | str:
    """Regimes from the benchmark bars of the snapshot, up to `end`; a reason string on failure.

    Read directly from the provider (not through a DataView): regimes are hindsight labels
    for attributing past trades, never an input to a strategy. Bars after `end` are dropped
    even if a provider returns them, so later data cannot reshape the regimes being judged.
    """
    symbol = thresholds.benchmark_symbol
    bars = [b for b in provider.bars([symbol], end) if b.ts <= end]
    if not bars:
        return f"cannot derive regimes: no {symbol} bars in the snapshot"
    if len(bars) < MIN_BENCHMARK_BARS:
        return f"cannot derive regimes: only {len(bars)} {symbol} bar in the snapshot"
    try:
        return derive_regimes(bars, min_drawdown=thresholds.regime_min_drawdown)
    except ValueError as err:
        return f"cannot derive regimes: {err}"


def _floors(
    base: BacktestResult, provider: DataProvider, end: datetime, thresholds: Thresholds
) -> StageResult:
    regimes = _derive_regimes(provider, end, thresholds)
    if isinstance(regimes, str):
        return StageResult(
            stage=Stage.FLOORS, passed=False, reason=regimes, metrics={"n_regimes": 0.0}
        )
    return floors_stage(base, regimes, thresholds)


def _fail(stage: Stage, reason: str) -> StageResult:
    return StageResult(stage=stage, passed=False, reason=reason, metrics={})


def run_pipeline(
    *,
    strategy: Strategy,
    build: Callable[[Mapping[str, Any]], Strategy] | None,
    params: Mapping[str, int | float],
    provider: DataProvider,
    symbols: Sequence[str],
    start: datetime,
    end: datetime,
    capital: CapitalConfig,
    registry: Registry,
    seed: int,
    cost_model: CostModel | None = None,
) -> PipelineResult:
    return _run_pipeline(
        strategy=strategy,
        build=build,
        params=params,
        provider=provider,
        symbols=symbols,
        start=start,
        end=end,
        capital=capital,
        registry=registry,
        seed=seed,
        cost_model=cost_model,
        thresholds=DEFAULT_THRESHOLDS,
    )


def _run_pipeline(
    *,
    strategy: Strategy,
    build: Callable[[Mapping[str, Any]], Strategy] | None,
    params: Mapping[str, int | float],
    provider: DataProvider,
    symbols: Sequence[str],
    start: datetime,
    end: datetime,
    capital: CapitalConfig,
    registry: Registry,
    seed: int,
    cost_model: CostModel | None = None,
    thresholds: Thresholds,
) -> PipelineResult:
    code_version = detect_code_version()
    trial_id = uuid.uuid4()  # needed before the trial row exists: the return series is filed by it
    stages: list[StageResult] = []
    metrics: dict[str, float] = {}
    current = Stage.LOOKAHEAD
    outcome = Outcome.ERROR
    base: BacktestResult | None = None
    snapshot_id, survivorship_biased = "unavailable", True

    def backtest(strat: Strategy, multiplier: float = 1.0) -> BacktestResult:
        return run_backtest(
            provider=provider,
            strategy=strat,
            symbols=symbols,
            start=start,
            end=end,
            capital=capital,
            cost_model=cost_model,
            cost_multiplier=multiplier,
            seed=seed,
            code_version=code_version,
        )

    def record(result: StageResult) -> bool:
        stages.append(result)
        metrics.update(_prefixed(result.stage, result.metrics))
        return result.passed

    try:
        try:
            snapshot = provider.snapshot()
            snapshot_id, survivorship_biased = snapshot.id, snapshot.survivorship_biased
        except Exception:
            logger.exception("snapshot unavailable for trial record")

        lookahead_early: StageResult | None = None
        try:
            base = backtest(strategy)
        except LookaheadError as err:
            lookahead_early = _fail(Stage.LOOKAHEAD, f"lookahead: strategy read the future: {err}")
        if base is not None:
            snapshot_id, survivorship_biased = base.snapshot_id, base.survivorship_biased

        def run_stages() -> StageResult | None:
            """Return the failing stage result, or None if all six pass."""
            nonlocal current
            assert base is not None
            current = Stage.LOOKAHEAD
            if not record(
                check_lookahead(
                    strategy,
                    provider,
                    symbols,
                    _check_times(base.timestamps, thresholds.lookahead_check_count),
                )
            ):
                return stages[-1]
            current = Stage.WALK_FORWARD
            if not record(walk_forward_stage(base, thresholds)):
                return stages[-1]
            current = Stage.TRIAL_ADJUSTED
            # The neighbours are backtested here only to record PBO; PBO never gates. Any
            # failure is kept and surfaces at the stability stage, where it belongs.
            runs = _ConfigurationRuns(strategy, build, params, base, backtest, thresholds)
            runs.prepare()
            peers, pbo_note = _store_and_load_peers(
                registry, trial_id, base, start, end, capital, thresholds
            )
            if not record(
                trial_adjusted_stage(
                    base,
                    registry,
                    peer_returns=peers,
                    neighbour_returns=runs.matrix(),
                    pbo_note=pbo_note,
                    n_trials=registry.count() + 1,
                    thresholds=thresholds,
                )
            ):
                return stages[-1]
            current = Stage.COST_SENSITIVITY
            if not record(
                cost_sensitivity_stage(base, lambda m: backtest(strategy, m), thresholds)
            ):
                return stages[-1]
            current = Stage.STABILITY
            if not record(runs.stability(thresholds)):
                return stages[-1]
            current = Stage.FLOORS
            if not record(_floors(base, provider, end, thresholds)):
                return stages[-1]
            return None

        if lookahead_early is not None:
            stages.append(lookahead_early)
            outcome = Outcome.REJECTED
        else:
            failed = run_stages()
            outcome = Outcome.REJECTED if failed is not None else Outcome.PASSED
    except Exception as err:
        logger.exception("pipeline stage %s raised", current.value)
        outcome = Outcome.ERROR
        if isinstance(err, _NanMetricError):
            metrics.update(err.partial)
        metrics["error"] = 1.0
        stages.append(_fail(current, f"error in {current.value}: {type(err).__name__}: {err}"))

    trial = _make_trial(
        trial_id=trial_id,
        strategy=strategy,
        params=params,
        capital=capital,
        snapshot_id=snapshot_id,
        survivorship_biased=survivorship_biased,
        stage=current,
        outcome=outcome,
        metrics=_trial_metrics(metrics, base),
    )
    registry.append(trial)
    return PipelineResult(stages=tuple(stages), stage_reached=current, outcome=outcome, trial=trial)


def _store_and_load_peers(
    registry: Registry,
    trial_id: uuid.UUID,
    base: BacktestResult,
    start: datetime,
    end: datetime,
    capital: CapitalConfig,
    thresholds: Thresholds,
) -> tuple[NDArray[np.float64] | None, str | None]:
    """File the candidate's returns under its group, then build the matrix PBO runs on.

    Returns (matrix, None), or (None, reason) when no matrix exists. Only a timestamp mismatch is
    absorbed (spec edge 2: the trial still logs with PBO not computed); any other registry
    failure propagates and ends the run as ERROR rather than quietly skipping the gate.
    """
    if not base.daily_returns:
        return None, "no daily returns to compare"
    series = ReturnSeries(
        trial_id=trial_id,
        group_key=group_key(
            snapshot_id=base.snapshot_id,
            start=start,
            end=end,
            cost_multiplier=base.cost_multiplier,
            capital_amount=capital.amount,
            capital_currency=capital.currency,
        ),
        timestamps=base.timestamps,
        returns=base.daily_returns,
    )
    try:
        registry.append_returns(series)
    except ReturnSeriesMismatchError:
        logger.warning("return series refused: timestamps differ from the group's")
        return None, "return series refused: its timestamps differ from the group's"
    return peer_matrix(registry, series, max_peers=thresholds.pbo_max_peers), None


REPRODUCTION_TOLERANCE = 1e-12


def _reproduces(submitted: BacktestResult, rebuilt: BacktestResult) -> bool:
    return submitted.timestamps == rebuilt.timestamps and bool(
        np.allclose(
            submitted.daily_returns, rebuilt.daily_returns, rtol=0.0, atol=REPRODUCTION_TOLERANCE
        )
    )


class _ConfigurationRuns:
    """The candidate and its neighbours, each backtested exactly once (candidate = base run).

    `prepare` is called at stage 3 to feed PBO and never raises: if the configurations cannot be
    produced, the reason (or the exception) is kept and `stability` reports it, so a problem with
    `build` or a neighbour is judged at the stability stage whatever PBO needed.
    """

    def __init__(
        self,
        strategy: Strategy,
        build: Callable[[Mapping[str, Any]], Strategy] | None,
        params: Mapping[str, int | float],
        base: BacktestResult,
        backtest: Callable[[Strategy], BacktestResult],
        thresholds: Thresholds,
    ) -> None:
        self._build = build
        self._params = params
        self._base = base
        self._backtest = backtest
        self._thresholds = thresholds
        self._results: list[BacktestResult] | None = None
        self._reason: str | None = None
        self._error: Exception | None = None
        self._prepared = False

    def prepare(self) -> None:
        if self._prepared:
            return
        self._prepared = True
        try:
            self._produce()
        except Exception as err:
            logger.exception("neighbour evaluation failed; reported at the stability stage")
            self._error = err

    def _produce(self) -> None:
        if self._build is None:
            self._reason = "no build function given; stability cannot be evaluated"
            return
        candidate = dict(self._params)

        # The neighbours are built by `build`, so `build` must first prove it reproduces the
        # submitted strategy at the candidate point. Otherwise a stable stand-in could be graded
        # in place of a fragile submission. This runs before any neighbour is evaluated.
        if not _reproduces(self._base, self._backtest(self._build(candidate))):
            self._reason = "build(params) does not reproduce the submitted strategy"
            return

        neighbours = neighbour_points(self._params, self._thresholds.stability_neighbor_fractions)
        if 1 + len(neighbours) < MIN_POINTS:
            self._reason = (
                f"{1 + len(neighbours)} configurations; at least {MIN_POINTS} "
                "(candidate plus neighbours) are needed to judge stability"
            )
            return
        results = [self._base, *(self._backtest(self._build(p)) for p in neighbours)]
        if any(r.timestamps != self._base.timestamps for r in results):
            self._reason = "configurations do not share the same timestamps"
            return
        self._results = results

    def matrix(self) -> NDArray[np.float64] | None:
        if self._results is None:
            return None
        return np.column_stack(
            [np.asarray(r.daily_returns, dtype=np.float64) for r in self._results]
        )

    def stability(self, thresholds: Thresholds) -> StageResult:
        """Judge the already-computed runs; nothing is backtested again here."""
        self.prepare()
        if self._error is not None:
            raise self._error
        if self._results is None:
            return _fail(Stage.STABILITY, self._reason or "configurations unavailable")
        results = self._results
        return stability_stage(
            list(range(len(results))), lambda i: results[i], lambda r: r, thresholds
        )


def _trial_metrics(metrics: dict[str, float], base: BacktestResult | None) -> dict[str, float]:
    out = dict(metrics)
    out["n_event_holds"] = float(sum(t.crossed_event for t in base.trades)) if base else 0.0
    returns = np.asarray(base.daily_returns if base is not None else (), dtype=np.float64)
    if len(returns) >= 2:
        out[SHARPE_METRIC] = sharpe_moments(returns)[0]
        out[SHARPE_MISSING_FLAG] = 0.0
    else:
        out[SHARPE_METRIC] = 0.0
        out[SHARPE_MISSING_FLAG] = 1.0
    return json_safe_metrics(out)


def _make_trial(
    *,
    trial_id: uuid.UUID,
    strategy: Strategy,
    params: Mapping[str, int | float],
    capital: CapitalConfig,
    snapshot_id: str,
    survivorship_biased: bool,
    stage: Stage,
    outcome: Outcome,
    metrics: Mapping[str, float],
) -> Trial:
    return Trial(
        id=trial_id,
        created_at=datetime.now(UTC),
        strategy_id=_strategy_id(strategy),
        code_hash=_code_hash(strategy),
        params=dict(params),
        capital_amount=capital.amount,
        capital_currency=capital.currency,
        data_snapshot_id=snapshot_id,
        survivorship_biased=survivorship_biased,
        stage_reached=stage,
        outcome=outcome,
        metrics=metrics,
    )
