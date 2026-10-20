import hashlib
import inspect
import math
import random
import uuid
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from contracts import CapitalConfig, Intent, Outcome, ReturnSeries, Stage
from contracts.data import Bar
from contracts.dataview import DataView
from fakes.data import InMemoryDataProvider
from fakes.registry import InMemoryRegistry
from harness.api import BacktestResult, TradeRecord
from harness.engine.runner import run_backtest
from harness.thresholds import Thresholds
from harness.validation import pipeline
from harness.validation.floors import floors_stage
from harness.validation.peers import group_key
from harness.validation.pipeline import (
    INFINITY_CAP,
    PipelineResult,
    _run_pipeline,
    json_safe_metrics,
    run_pipeline,
)
from tests.helpers import make_trial

START = datetime(2020, 1, 1, 21, tzinfo=UTC)
END = START + timedelta(days=149)
SYMBOLS = ["AAA"]
CAPITAL = CapitalConfig(effective_at=START, source="default")
PARAMS: dict[str, int | float] = {"hold": 3, "flat": 2}
ORDER = [
    Stage.LOOKAHEAD,
    Stage.WALK_FORWARD,
    Stage.TRIAL_ADJUSTED,
    Stage.COST_SENSITIVITY,
    Stage.STABILITY,
    Stage.FLOORS,
]

LENIENT = Thresholds(
    lookahead_check_count=5,
    wf_min_oos_sharpe=-100.0,
    wf_min_positive_fold_fraction=0.1,
    dsr_min_probability=0.01,
    cost_min_profit_factor=0.01,
    stability_tolerance=0.99,
    min_trades=1,
    min_profitable_regimes=1,
    pbo_max=1.0,
)

DEFAULT_SPY = [100.0 * 1.003**d for d in range(150)]  # one long advance


def spy_bars(closes: list[float]) -> list[Bar]:
    return [
        Bar(
            symbol="SPY",
            ts=START + timedelta(days=d),
            open=c,
            high=c,
            low=c,
            close=c,
            volume=1e6,
        )
        for d, c in enumerate(closes)
    ]


def make_provider(spy_closes: list[float] | None = DEFAULT_SPY) -> InMemoryDataProvider:
    rng = random.Random(1)
    price = 100.0
    bars = []
    for day in range(150):
        close = price * (1 + 0.004 + rng.gauss(0, 0.003))
        bars.append(
            Bar(
                symbol="AAA",
                ts=START + timedelta(days=day),
                open=price,
                high=max(price, close) * 1.001,
                low=min(price, close) * 0.999,
                close=close,
                volume=1e6,
            )
        )
        price = close
    if spy_closes is not None:
        bars.extend(spy_bars(spy_closes))
    return InMemoryDataProvider(bars)


class Cycle:
    """Holds AAA for `hold` days then sits out `flat` days, by calendar position only."""

    strategy_id = "cycle"

    def __init__(self, hold: int, flat: int) -> None:
        self.hold, self.flat = hold, flat

    def generate(self, data: DataView, asof: datetime) -> Intent:
        k = (asof - START).days % (self.hold + self.flat)
        return Intent(asof=asof, weights={"AAA": 1.0} if k < self.hold else {})


def build(params: Mapping[str, Any]) -> Cycle:
    return Cycle(int(params["hold"]), int(params["flat"]))


class Peeker:
    """Leaks: holds AAA only if tomorrow's close is higher, read around the DataView."""

    strategy_id = "peek"

    def generate(self, data: DataView, asof: datetime) -> Intent:
        provider = data._provider
        nxt = [b for b in provider.bars(SYMBOLS, asof + timedelta(days=1)) if b.ts > asof]
        today = [b for b in provider.bars(SYMBOLS, asof) if b.ts == asof]
        up = bool(nxt and today and nxt[0].close > today[0].close)
        return Intent(asof=asof, weights={"AAA": 1.0} if up else {})


class Asker:
    strategy_id = "ask"

    def generate(self, data: DataView, asof: datetime) -> Intent:
        data.bars(SYMBOLS, asof + timedelta(days=1))
        return Intent(asof=asof, weights={})


class Exploder:
    strategy_id = "boom"

    def generate(self, data: DataView, asof: datetime) -> Intent:
        raise RuntimeError("strategy bug")


def run(
    thresholds: Thresholds = LENIENT,
    registry: InMemoryRegistry | None = None,
    strategy: Any = None,
    builder: Any = build,
    provider: Any = None,
) -> tuple[PipelineResult, InMemoryRegistry]:
    registry = registry if registry is not None else InMemoryRegistry()
    result = _run_pipeline(
        strategy=strategy if strategy is not None else Cycle(3, 2),
        build=builder,
        params=PARAMS,
        provider=provider if provider is not None else make_provider(),
        symbols=SYMBOLS,
        start=START,
        end=END,
        capital=CAPITAL,
        registry=registry,
        seed=0,
        thresholds=thresholds,
    )
    return result, registry


def assert_logged_once(result: PipelineResult, registry: InMemoryRegistry, before: int) -> None:
    assert registry.count() == before + 1
    assert registry.get(result.trial.id) == result.trial
    assert result.trial.stage_reached is result.stage_reached
    assert result.trial.outcome is result.outcome
    assert "sharpe_per_period" in result.trial.metrics
    assert all(math.isfinite(v) for v in result.trial.metrics.values())


def populated(n: int, sharpe: float | None = None) -> InMemoryRegistry:
    registry = InMemoryRegistry()
    for i in range(n):
        value = None if sharpe is None else sharpe * (1 if i % 2 else -1)
        metrics = {} if value is None else {"sharpe_per_period": value}
        registry.append(make_trial(id=uuid.uuid4(), metrics=metrics))
    return registry


# --- passing and the fixed order -------------------------------------------------------------


def test_all_six_stages_pass_in_the_fixed_order() -> None:
    result, registry = run()
    assert [s.stage for s in result.stages] == ORDER
    assert all(s.passed for s in result.stages), [s.reason for s in result.stages]
    assert result.stage_reached is Stage.FLOORS
    assert result.outcome is Outcome.PASSED
    assert_logged_once(result, registry, before=0)
    for stage in ORDER:
        assert any(k.startswith(f"{stage.value}.") for k in result.trial.metrics)


# --- one rejection per stage; exactly one Trial each -----------------------------------------


def test_rejected_at_lookahead() -> None:
    result, registry = run(strategy=Peeker())
    assert [s.stage for s in result.stages] == [Stage.LOOKAHEAD]
    assert result.stage_reached is Stage.LOOKAHEAD
    assert result.outcome is Outcome.REJECTED
    assert_logged_once(result, registry, 0)


def test_a_strategy_that_asks_the_view_for_the_future_is_rejected_at_lookahead() -> None:
    result, registry = run(strategy=Asker())
    assert result.stage_reached is Stage.LOOKAHEAD
    assert result.outcome is Outcome.REJECTED
    assert result.trial.metrics["sharpe_per_period_missing"] == 1.0
    assert_logged_once(result, registry, 0)


def test_rejected_at_walk_forward() -> None:
    result, registry = run(Thresholds(**{**LENIENT.model_dump(), "wf_min_oos_sharpe": 1000.0}))
    assert result.stage_reached is Stage.WALK_FORWARD
    assert result.outcome is Outcome.REJECTED
    assert [s.stage for s in result.stages] == ORDER[:2]
    assert_logged_once(result, registry, 0)


def test_rejected_at_trial_adjusted() -> None:
    registry = populated(200, sharpe=1.0)
    result, _ = run(registry=registry)
    assert result.stage_reached is Stage.TRIAL_ADJUSTED
    assert result.outcome is Outcome.REJECTED
    assert [s.stage for s in result.stages] == ORDER[:3]
    assert_logged_once(result, registry, 200)


def test_rejected_at_cost_sensitivity() -> None:
    harsh = Thresholds(
        **{**LENIENT.model_dump(), "cost_multiplier": 5000.0, "cost_min_profit_factor": 1.0}
    )
    result, registry = run(harsh)
    assert result.stage_reached is Stage.COST_SENSITIVITY
    assert result.outcome is Outcome.REJECTED
    assert [s.stage for s in result.stages] == ORDER[:4]
    assert_logged_once(result, registry, 0)


def test_rejected_at_stability_when_build_is_missing() -> None:
    result, registry = run(builder=None)
    assert result.stage_reached is Stage.STABILITY
    assert result.outcome is Outcome.REJECTED
    assert "build" in result.stages[-1].reason
    assert [s.stage for s in result.stages] == ORDER[:5]
    assert result.trial.metrics["trial_adjusted.pbo_computed"] == 0.0
    assert_logged_once(result, registry, 0)


def test_rejected_at_stability_when_there_are_too_few_configurations() -> None:
    registry = InMemoryRegistry()
    result = _run_pipeline(
        strategy=Cycle(3, 2),
        build=lambda _p: Cycle(3, 2),
        params={},
        provider=make_provider(),
        symbols=SYMBOLS,
        start=START,
        end=END,
        capital=CAPITAL,
        registry=registry,
        seed=0,
        thresholds=LENIENT,
    )
    assert result.stage_reached is Stage.STABILITY
    assert result.outcome is Outcome.REJECTED
    assert result.trial.metrics["trial_adjusted.pbo_computed"] == 0.0
    assert_logged_once(result, registry, 0)


def test_pbo_that_cannot_be_computed_does_not_fail_trial_adjusted() -> None:
    registry = InMemoryRegistry()
    result = _run_pipeline(
        strategy=Cycle(3, 2),
        build=build,
        params=PARAMS,
        provider=make_provider(),
        symbols=SYMBOLS,
        start=START,
        end=START + timedelta(days=19),  # 20 rows: too few for the CSCV partitioning
        capital=CAPITAL,
        registry=registry,
        seed=0,
        thresholds=LENIENT,
    )
    adjusted = [s for s in result.stages if s.stage is Stage.TRIAL_ADJUSTED]
    assert adjusted
    assert adjusted[0].passed
    assert result.trial.metrics["trial_adjusted.pbo_computed"] == 0.0
    assert result.trial.metrics["trial_adjusted.pbo_gating"] == 0.0
    assert_logged_once(result, registry, 0)


def test_rejected_at_floors() -> None:
    result, registry = run(Thresholds(**{**LENIENT.model_dump(), "min_trades": 10_000}))
    assert result.stage_reached is Stage.FLOORS
    assert result.outcome is Outcome.REJECTED
    assert [s.stage for s in result.stages] == ORDER
    assert_logged_once(result, registry, 0)


# --- errors are logged, never skipped ---------------------------------------------------------


def test_an_exception_in_the_candidate_is_logged_as_an_error() -> None:
    result, registry = run(strategy=Exploder())
    assert result.outcome is Outcome.ERROR
    assert result.stage_reached is Stage.LOOKAHEAD
    assert result.trial.metrics["sharpe_per_period_missing"] == 1.0
    assert not result.stages[-1].passed
    assert "strategy bug" in result.stages[-1].reason
    assert_logged_once(result, registry, 0)


def test_an_exception_inside_a_later_stage_records_that_stage() -> None:
    def exploding_build(params: Mapping[str, Any]) -> Cycle:
        if params["hold"] != 3:
            raise RuntimeError("bad neighbour")
        return build(params)

    result, registry = run(builder=exploding_build)
    assert result.outcome is Outcome.ERROR
    assert result.stage_reached is Stage.STABILITY  # the neighbour error surfaces where it matters
    assert [s.stage for s in result.stages][:4] == ORDER[:4]
    assert "bad neighbour" in result.stages[-1].reason
    assert result.trial.metrics["sharpe_per_period_missing"] == 0.0  # base run existed
    assert_logged_once(result, registry, 0)


def test_a_failing_trial_append_is_not_swallowed() -> None:
    class Broken(InMemoryRegistry):
        def append(self, trial: Any) -> None:
            raise OSError("disk full")

    with pytest.raises(OSError, match="disk full"):
        run(registry=Broken())


# --- trial count ------------------------------------------------------------------------------


@pytest.mark.parametrize("existing", [0, 4])
def test_the_candidate_counts_exactly_once_in_the_trial_count(existing: int) -> None:
    result, _ = run(registry=populated(existing))
    assert result.trial.metrics["trial_adjusted.n_trials"] == existing + 1


def test_pbo_is_computed_and_recorded_on_a_normal_run() -> None:
    result, _ = run()
    assert result.trial.metrics["trial_adjusted.pbo_neighbours_computed"] == 1.0
    assert result.trial.metrics["trial_adjusted.pbo_gating"] == 0.0
    assert 0.0 <= result.trial.metrics["trial_adjusted.pbo_neighbours"] <= 1.0
    assert result.trial.metrics["trial_adjusted.pbo_computed"] == 0.0  # no peers yet


# --- trial contents ---------------------------------------------------------------------------


def test_trial_identity_fields() -> None:
    result, _ = run()
    trial = result.trial
    assert trial.strategy_id == f"{Cycle.__module__}.{Cycle.__qualname__}"
    assert trial.code_hash == hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    assert dict(trial.params) == PARAMS
    assert trial.capital_amount == CAPITAL.amount
    assert trial.capital_currency == CAPITAL.currency
    assert trial.data_snapshot_id == make_provider().snapshot().id
    assert trial.survivorship_biased is True


def test_code_hash_falls_back_with_an_explicit_marker() -> None:
    Dynamic = type("Dynamic", (Cycle,), {"__module__": "builtins"})
    result, _ = run(strategy=Dynamic(3, 2))
    assert result.trial.code_hash == "unavailable:builtins.Dynamic"


def test_stage_metrics_are_prefixed_with_the_stage_name() -> None:
    result, _ = run()
    for key in result.trial.metrics:
        assert key.startswith(tuple(f"{s.value}." for s in ORDER)) or key.startswith(
            ("sharpe_per_period", "n_event_holds")
        )


# --- JSON safety ------------------------------------------------------------------------------


def test_infinity_is_capped_and_flagged() -> None:
    clean = json_safe_metrics({"profit_factor": math.inf, "loss": -math.inf, "ok": 2.0})
    assert clean["profit_factor"] == INFINITY_CAP
    assert clean["profit_factor_capped"] == 1.0
    assert clean["loss"] == -INFINITY_CAP
    assert clean["loss_capped"] == 1.0
    assert clean["ok"] == 2.0
    assert "ok_capped" not in clean
    assert math.isfinite(INFINITY_CAP)


def test_nan_is_rejected_not_stored() -> None:
    with pytest.raises(ValueError, match="NaN"):
        json_safe_metrics({"sharpe": math.nan})


def test_nan_from_a_stage_becomes_a_logged_error(monkeypatch: pytest.MonkeyPatch) -> None:
    real = floors_stage

    def nan_floors(*args: Any, **kwargs: Any) -> Any:
        stage = real(*args, **kwargs)
        return stage.model_copy(update={"metrics": {"n_trades": math.nan}})

    monkeypatch.setattr(pipeline, "floors_stage", nan_floors)
    result, registry = run()
    assert result.outcome is Outcome.ERROR
    assert result.stage_reached is Stage.FLOORS
    assert result.trial.metrics["floors.n_trades_nan"] == 1.0
    assert "floors.n_trades" not in result.trial.metrics
    assert_logged_once(result, registry, 0)


# --- public entry point -----------------------------------------------------------------------


def test_public_entry_point_has_no_thresholds_parameter() -> None:
    names = set(inspect.signature(run_pipeline).parameters)
    assert "thresholds" not in names
    assert names == {
        "strategy", "build", "params", "provider", "symbols", "start", "end", "capital",
        "registry", "seed", "cost_model",
    }  # fmt: skip
    assert all(
        p.kind is inspect.Parameter.KEYWORD_ONLY
        for p in inspect.signature(run_pipeline).parameters.values()
    )


def test_public_entry_point_always_uses_the_default_thresholds() -> None:
    registry = InMemoryRegistry()
    result = run_pipeline(
        strategy=Cycle(3, 2),
        build=build,
        params=PARAMS,
        provider=make_provider(),
        symbols=SYMBOLS,
        start=START,
        end=END,
        capital=CAPITAL,
        registry=registry,
        seed=0,
    )
    # 150 days cannot produce the default 100-trade floor or clear the default DSR/regime rules.
    assert result.outcome is Outcome.REJECTED
    assert registry.count() == 1
    assert registry.get(result.trial.id) == result.trial


# --- build(params) must reproduce the submitted strategy --------------------------------------


def test_a_matching_build_passes_the_reproduction_check() -> None:
    result, _ = run()
    assert result.stage_reached is Stage.FLOORS
    assert result.outcome is Outcome.PASSED


def test_a_build_that_returns_a_different_strategy_fails_stability() -> None:
    def stable_impostor(params: Mapping[str, Any]) -> Cycle:
        return Cycle(2, 2)

    result, registry = run(builder=stable_impostor)
    assert result.stage_reached is Stage.STABILITY
    assert result.outcome is Outcome.REJECTED
    assert "does not reproduce the submitted strategy" in result.stages[-1].reason
    assert_logged_once(result, registry, 0)


def test_a_build_that_ignores_params_fails_stability() -> None:
    def ignores_params(params: Mapping[str, Any]) -> Cycle:
        return Cycle(4, 2)  # submitted is Cycle(3, 2) and params say hold=3

    result, _ = run(builder=ignores_params)
    assert result.stage_reached is Stage.STABILITY
    assert "does not reproduce" in result.stages[-1].reason


def test_the_reproduction_check_runs_before_any_neighbour_is_built() -> None:
    built: list[Mapping[str, Any]] = []

    def recording(params: Mapping[str, Any]) -> Cycle:
        built.append(dict(params))
        return Cycle(2, 2)

    run(builder=recording)
    assert built == [PARAMS]  # only the candidate point; no neighbour was evaluated


def test_no_configuration_is_backtested_twice(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: dict[int, list[float]] = {}
    keep_alive: list[Any] = []  # ids must not be reused by freed strategies
    real = run_backtest

    def counting(**kwargs: Any) -> Any:
        keep_alive.append(kwargs["strategy"])
        calls.setdefault(id(kwargs["strategy"]), []).append(kwargs["cost_multiplier"])
        return real(**kwargs)

    monkeypatch.setattr(pipeline, "run_backtest", counting)
    submitted = Cycle(3, 2)
    result, _ = run(strategy=submitted)
    assert result.outcome is Outcome.PASSED
    assert calls[id(submitted)] == [1.0, 2.0]  # base and cost run; PBO and stability reuse base
    others = [m for key, m in calls.items() if key != id(submitted)]
    assert all(m == [1.0] for m in others)
    assert len(others) == 1 + 4  # build(params) reproduction run plus four neighbours


def test_missing_build_still_fails_closed() -> None:
    result, _ = run(builder=None)
    assert result.outcome is Outcome.REJECTED


# --- scripted configurations: PBO and stability decided by controlled return series -----------

N_DAYS = 150


class Scripted(Cycle):
    def __init__(self, series: list[float], event_holds: int = 0) -> None:
        super().__init__(3, 2)
        self.series = series
        self.event_holds = event_holds


def noise(seed: int, mean: float = 0.0) -> list[float]:
    return [float(x) for x in np.random.default_rng(seed).normal(mean, 0.01, N_DAYS)]


def scripted_backtest(**kwargs: Any) -> BacktestResult:
    strategy: Scripted = kwargs["strategy"]
    ts = tuple(START + timedelta(days=i) for i in range(N_DAYS))
    exit_ts = START + timedelta(days=2)
    trades = tuple(
        TradeRecord(
            symbol="AAA", entry_ts=START, exit_ts=exit_ts, entry_price=1, exit_price=1,
            shares=1, cost=0, pnl=pnl, crossed_event=i < strategy.event_holds,
        )
        for i, pnl in enumerate([2.0, -1.0])
    )  # fmt: skip
    return BacktestResult(
        capital=kwargs["capital"],
        snapshot_id="scripted",
        cost_multiplier=kwargs["cost_multiplier"],
        seed=0,
        code_version="v",
        survivorship_biased=True,
        timestamps=ts,
        daily_returns=tuple(strategy.series),
        trades=trades,
    )


def run_scripted(
    candidate: list[float],
    neighbour_series: Callable[[Mapping[str, Any]], list[float]],
    thresholds: Thresholds = LENIENT,
    event_holds: int = 0,
    registry: InMemoryRegistry | None = None,
) -> tuple[PipelineResult, InMemoryRegistry]:
    registry = registry if registry is not None else InMemoryRegistry()

    def scripted_build(params: Mapping[str, Any]) -> Scripted:
        series = candidate if dict(params) == PARAMS else neighbour_series(params)
        return Scripted(series, event_holds if dict(params) == PARAMS else 0)

    result = _run_pipeline(
        strategy=Scripted(candidate, event_holds),
        build=scripted_build,
        params=PARAMS,
        provider=make_provider(),
        symbols=SYMBOLS,
        start=START,
        end=END,
        capital=CAPITAL,
        registry=registry,
        seed=0,
        thresholds=thresholds,
    )
    return result, registry


@pytest.fixture
def scripted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pipeline, "run_backtest", scripted_backtest)


def seed_of(params: Mapping[str, Any]) -> int:
    return int(params["hold"]) * 100 + int(params["flat"])


def test_neighbour_pbo_above_the_maximum_is_recorded_but_does_not_gate(scripted: None) -> None:
    half = N_DAYS // 2
    edge = 0.02
    candidate = [edge if i < half else -edge for i in range(N_DAYS)]  # wins early, loses late

    def neighbour(params: Mapping[str, Any]) -> list[float]:
        wobble = noise(seed_of(params))
        return [(-edge if i < half else edge) + 0.001 * wobble[i] for i in range(N_DAYS)]

    result, _ = run_scripted(
        candidate, neighbour, Thresholds(**{**LENIENT.model_dump(), "pbo_max": 0.5})
    )
    assert result.trial.metrics["trial_adjusted.pbo_neighbours"] > 0.5
    assert result.trial.metrics["trial_adjusted.pbo_gating"] == 0.0
    adjusted = next(s for s in result.stages if s.stage is Stage.TRIAL_ADJUSTED)
    assert adjusted.passed  # DSR alone decides this stage
    assert result.trial.metrics["trial_adjusted.dsr"] >= LENIENT.dsr_min_probability


def test_a_dominant_candidate_has_low_pbo_but_fragile_neighbours_fail_stability(
    scripted: None,
) -> None:
    candidate = noise(1, mean=0.003)

    def neighbour(params: Mapping[str, Any]) -> list[float]:
        return noise(seed_of(params), mean=0.0004)

    result, registry = run_scripted(
        candidate, neighbour, Thresholds(**{**LENIENT.model_dump(), "pbo_max": 0.5})
    )
    assert result.trial.metrics["trial_adjusted.pbo_neighbours"] < 0.5
    assert result.stage_reached is Stage.STABILITY
    assert result.outcome is Outcome.REJECTED
    assert [s.stage for s in result.stages] == ORDER[:5]
    assert_logged_once(result, registry, 0)


# --- flagged, not enforced: event holds -------------------------------------------------------


def test_event_holds_are_counted_in_the_trial_metrics(scripted: None) -> None:
    result, _ = run_scripted(noise(1, 0.003), lambda p: noise(seed_of(p), 0.003), event_holds=2)
    assert result.trial.metrics["n_event_holds"] == 2.0


def test_event_holds_is_zero_when_there_are_none_or_no_result_exists() -> None:
    result, _ = run()
    assert result.trial.metrics["n_event_holds"] == 0.0
    boom, _ = run(strategy=Exploder())
    assert boom.trial.metrics["n_event_holds"] == 0.0


def test_floors_metric_shows_trades_outside_the_regime_table() -> None:
    result, _ = run()
    assert result.trial.metrics["floors.trades_outside_regimes"] == 0.0


# --- regimes are derived from the benchmark in the snapshot -----------------------------------


def selloff_then_recovery(selloff_start: int) -> list[float]:
    """Advance, a 30% selloff beginning at a record close on `selloff_start`, then new records."""
    closes: list[float] = []
    for d in range(150):
        if d <= selloff_start:
            closes.append(100.0 + d)
        elif d <= selloff_start + 20:
            closes.append((100.0 + selloff_start) * (1 - 0.015 * (d - selloff_start)))
        else:
            closes.append((100.0 + selloff_start) * 0.7 + 3.0 * (d - selloff_start - 20))
    return closes


def test_floors_fail_closed_without_benchmark_bars() -> None:
    result, registry = run(provider=make_provider(spy_closes=None))
    assert result.outcome is Outcome.REJECTED
    assert result.stage_reached is Stage.FLOORS
    assert "cannot derive regimes" in result.stages[-1].reason
    assert "SPY" in result.stages[-1].reason
    assert [s.stage for s in result.stages] == ORDER  # every earlier stage ran and passed
    assert all(s.passed for s in result.stages[:-1])
    assert result.trial.metrics["floors.n_regimes"] == 0.0
    assert_logged_once(result, registry, 0)


def test_floors_fail_closed_with_a_single_benchmark_bar() -> None:
    result, registry = run(provider=make_provider(spy_closes=[100.0]))
    assert result.outcome is Outcome.REJECTED  # a rejection, not an ERROR
    assert result.stage_reached is Stage.FLOORS
    assert "cannot derive regimes" in result.stages[-1].reason
    assert_logged_once(result, registry, 0)


def test_derived_regimes_are_what_floors_receives(monkeypatch: pytest.MonkeyPatch) -> None:
    from harness.data.regimes import derive_regimes

    seen: list[Any] = []
    real = floors_stage

    def capture(res: Any, regimes: Any, *args: Any, **kwargs: Any) -> Any:
        seen.append(tuple(regimes))
        return real(res, regimes, *args, **kwargs)

    monkeypatch.setattr(pipeline, "floors_stage", capture)
    spy = selloff_then_recovery(60)
    result, _ = run(provider=make_provider(spy_closes=spy))
    expected = derive_regimes(spy_bars(spy), min_drawdown=LENIENT.regime_min_drawdown)
    assert seen == [expected]
    assert any(r.name.startswith("selloff_") for r in expected)
    assert result.trial.metrics["floors.n_regimes"] == float(len(expected))


def test_a_trade_is_attributed_to_the_derived_selloff_or_advance_regime(scripted: None) -> None:
    # The scripted trades exit on day 2 (+1.0 net). A selloff that starts at day 0 puts them in
    # the selloff regime; a late selloff leaves them in the first advance.
    early, _ = run_scripted_with_spy(selloff_then_recovery(0))
    late, _ = run_scripted_with_spy(selloff_then_recovery(100))

    def pnl(result: PipelineResult, kind: str) -> list[float]:
        return [v for k, v in result.trial.metrics.items() if f"floors.regime_pnl.{kind}_" in k]

    assert pnl(early, "selloff") == [1.0]
    assert all(v == 0.0 for v in pnl(early, "advance"))
    assert pnl(late, "advance")[0] == 1.0
    assert all(v == 0.0 for v in pnl(late, "selloff"))


def run_scripted_with_spy(spy: list[float]) -> tuple[PipelineResult, InMemoryRegistry]:
    registry = InMemoryRegistry()
    candidate = noise(1, mean=0.003)
    result = _run_pipeline(
        strategy=Scripted(candidate),
        build=lambda p: Scripted(candidate if dict(p) == PARAMS else noise(seed_of(p), 0.003)),
        params=PARAMS,
        provider=make_provider(spy_closes=spy),
        symbols=SYMBOLS,
        start=START,
        end=END,
        capital=CAPITAL,
        registry=registry,
        seed=0,
        thresholds=LENIENT,
    )
    return result, registry


class RecordingProvider(InMemoryDataProvider):
    def __init__(self, bars: list[Bar]) -> None:
        super().__init__(bars)
        self.ends: list[tuple[tuple[str, ...], datetime]] = []

    def bars(self, symbols: Any, end: datetime) -> Any:
        self.ends.append((tuple(symbols), end))
        return super().bars(symbols, end)


def test_regime_derivation_never_reads_benchmark_bars_after_end() -> None:
    long_spy = DEFAULT_SPY + [DEFAULT_SPY[-1] * 0.5] * 30  # a crash AFTER the pipeline's end
    base_provider = make_provider(spy_closes=long_spy)
    provider = RecordingProvider(list(base_provider._bars))
    result, _ = run(provider=provider)
    spy_reads = [end for symbols, end in provider.ends if symbols == ("SPY",)]
    assert spy_reads
    assert all(end <= END for end in spy_reads)
    assert result.outcome is Outcome.PASSED  # the post-end crash did not create a selloff
    assert result.trial.metrics["floors.n_regimes"] == 1.0


def test_bars_after_end_from_a_leaky_provider_are_ignored() -> None:
    class Ignoring(InMemoryDataProvider):
        def bars(self, symbols: Any, end: datetime) -> Any:
            if list(symbols) == ["SPY"]:
                end = datetime(9999, 1, 1, tzinfo=UTC)  # a buggy provider that ignores `end`
            return super().bars(symbols, end)

    long_spy = DEFAULT_SPY + [DEFAULT_SPY[-1] * 0.5] * 30
    base_provider = make_provider(spy_closes=long_spy)
    result, _ = run(provider=Ignoring(list(base_provider._bars)))
    assert result.trial.metrics["floors.n_regimes"] == 1.0


# --- cross-trial PBO: peers are earlier trials of the same group ---------------------------------

PEER_MIN = 6
PEER_THRESHOLDS = Thresholds(
    **{**LENIENT.model_dump(), "pbo_min_trials": PEER_MIN, "pbo_n_blocks": 8, "pbo_max": 0.5}
)
EDGE = 0.02
HALF = N_DAYS // 2
WINS_EARLY = [EDGE if i < HALF else -EDGE for i in range(N_DAYS)]


def scripted_group() -> str:
    return group_key(
        snapshot_id="scripted",
        start=START,
        end=END,
        cost_multiplier=1.0,
        capital_amount=CAPITAL.amount,
        capital_currency=CAPITAL.currency,
    )


def seed_peers(registry: InMemoryRegistry, n: int, *, days: int = N_DAYS) -> None:
    """Two camps: odd peers win early like the candidate, even peers win late.

    In a balanced field the in-sample winner of one camp is the out-of-sample loser, which is what
    an overfit search looks like.
    """
    for i in range(n):
        wobble = noise(500 + i)
        sign = 1 if i % 2 else -1
        registry.append_returns(
            ReturnSeries(
                trial_id=uuid.uuid4(),
                group_key=scripted_group(),
                timestamps=tuple(START + timedelta(days=d) for d in range(days)),
                returns=tuple(
                    sign * (EDGE if d < HALF else -EDGE) + 0.001 * wobble[d] for d in range(days)
                ),
            )
        )


def neighbour_noise(params: Mapping[str, Any]) -> list[float]:
    return noise(seed_of(params))


def test_a_trial_stores_its_returns_under_its_own_id(scripted: None) -> None:
    result, registry = run_scripted(WINS_EARLY, neighbour_noise, PEER_THRESHOLDS)
    (stored,) = registry.returns_for_group(scripted_group(), limit=10)
    assert stored.trial_id == result.trial.id
    assert stored.returns == tuple(WINS_EARLY)


def test_cross_trial_pbo_gates_and_rejects_once_the_group_is_large_enough(scripted: None) -> None:
    registry = InMemoryRegistry()
    seed_peers(registry, PEER_MIN - 1)
    result, _ = run_scripted(WINS_EARLY, neighbour_noise, PEER_THRESHOLDS, registry=registry)
    assert result.stage_reached is Stage.TRIAL_ADJUSTED
    assert result.outcome is Outcome.REJECTED
    assert "PBO" in result.stages[-1].reason
    metrics = result.trial.metrics
    assert metrics["trial_adjusted.pbo_gating"] == 1.0
    assert metrics["trial_adjusted.pbo"] > PEER_THRESHOLDS.pbo_max
    assert metrics["trial_adjusted.pbo_group_size"] == PEER_MIN  # candidate counted once
    # A rejected trial still counts toward the next trial's real N.
    assert len(registry.returns_for_group(scripted_group(), limit=100)) == PEER_MIN


def test_below_the_minimum_group_size_the_trial_is_recorded_not_gated(scripted: None) -> None:
    registry = InMemoryRegistry()
    seed_peers(registry, PEER_MIN - 2)
    result, _ = run_scripted(WINS_EARLY, neighbour_noise, PEER_THRESHOLDS, registry=registry)
    adjusted = next(s for s in result.stages if s.stage is Stage.TRIAL_ADJUSTED)
    assert adjusted.passed
    assert result.trial.metrics["trial_adjusted.pbo_gating"] == 0.0
    assert result.trial.metrics["trial_adjusted.pbo_group_size"] == PEER_MIN - 1


def test_the_candidate_is_never_its_own_peer(scripted: None) -> None:
    registry = InMemoryRegistry()
    seed_peers(registry, 3)
    result, _ = run_scripted(WINS_EARLY, neighbour_noise, PEER_THRESHOLDS, registry=registry)
    assert result.trial.metrics["trial_adjusted.pbo_group_size"] == 4


def test_a_series_that_does_not_match_the_group_is_not_computed_and_not_fatal(
    scripted: None,
) -> None:
    registry = InMemoryRegistry()
    seed_peers(registry, PEER_MIN - 1, days=N_DAYS - 1)  # calendar differs from the candidate's
    result, _ = run_scripted(WINS_EARLY, neighbour_noise, PEER_THRESHOLDS, registry=registry)
    adjusted = next(s for s in result.stages if s.stage is Stage.TRIAL_ADJUSTED)
    assert adjusted.passed
    assert "refused" in adjusted.reason
    assert result.trial.metrics["trial_adjusted.pbo_computed"] == 0.0
    assert result.trial.metrics["trial_adjusted.pbo_gating"] == 0.0
    assert registry.count() == 1  # the trial still logged
    assert all(
        s.trial_id != result.trial.id
        for s in registry.returns_for_group(scripted_group(), limit=99)
    )


def test_trials_that_never_reach_stage_three_store_no_series(scripted: None) -> None:
    harsh_walk_forward = Thresholds(**{**PEER_THRESHOLDS.model_dump(), "wf_min_oos_sharpe": 1000.0})
    result, registry = run_scripted(WINS_EARLY, neighbour_noise, harsh_walk_forward)
    assert result.stage_reached is Stage.WALK_FORWARD
    assert registry.returns_for_group(scripted_group(), limit=10) == []


def test_groups_do_not_mix_across_windows(scripted: None) -> None:
    registry = InMemoryRegistry()
    seed_peers(registry, PEER_MIN - 1)
    other_end = END + timedelta(days=1)
    result = _run_pipeline(
        strategy=Scripted(WINS_EARLY),
        build=lambda p: Scripted(WINS_EARLY if dict(p) == PARAMS else noise(seed_of(p))),
        params=PARAMS,
        provider=make_provider(),
        symbols=SYMBOLS,
        start=START,
        end=other_end,
        capital=CAPITAL,
        registry=registry,
        seed=0,
        thresholds=PEER_THRESHOLDS,
    )
    assert result.trial.metrics["trial_adjusted.pbo_group_size"] == 1
