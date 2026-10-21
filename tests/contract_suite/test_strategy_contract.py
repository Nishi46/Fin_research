"""Conformance suite for `ParametrizedStrategy` (spec: .planning/specs/strategy-interface.md).

Each rule is a reusable `check_*` function that raises AssertionError naming itself. Any strategy
in `strategies/` should be run through `check_conformance`. Deliberately broken strategies below
prove that each check catches what it claims to.
"""

from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timedelta
from types import MappingProxyType

import pytest

from contracts import Intent, ParametrizedStrategy, Params
from contracts.dataview import DataView
from fakes.data import InMemoryDataProvider, synthetic_bars
from strategies.baseline import BaselineReversal
from tests.helpers import T0

MakeView = Callable[[datetime], DataView]
SYMBOLS = ["AAA", "BBB", "CCC", "DDD"]
SESSIONS = 40


def _fail(check: str, message: str) -> AssertionError:
    return AssertionError(f"{check}: {message}")


def _asofs() -> list[datetime]:
    return [T0 + timedelta(days=d) for d in (12, 20, 27, 35)]


def _run(
    strategy: ParametrizedStrategy, make_view: MakeView, asofs: Sequence[datetime]
) -> list[Intent]:
    return [strategy.generate(make_view(asof), asof) for asof in asofs]


def check_protocol(strategy: object) -> None:
    if not isinstance(strategy, ParametrizedStrategy):
        raise _fail("check_protocol", "does not satisfy ParametrizedStrategy")


def check_params_round_trip(strategy: ParametrizedStrategy, make_view: MakeView) -> None:
    expected = _run(strategy, make_view, _asofs())
    rebuilt = strategy.with_params(dict(strategy.params))
    if _run(rebuilt, make_view, _asofs()) != expected:
        raise _fail("check_params_round_trip", "with_params(params) does not reproduce the intents")


def check_with_params_does_not_mutate(strategy: ParametrizedStrategy, make_view: MakeView) -> None:
    name = "check_with_params_does_not_mutate"
    before_params = dict(strategy.params)
    before_intents = _run(strategy, make_view, _asofs())
    argument = dict(strategy.params)
    snapshot = dict(argument)
    strategy.with_params(argument)
    if argument != snapshot:
        raise _fail(name, "with_params mutated the mapping it was given")
    if dict(strategy.params) != before_params:
        raise _fail(name, "with_params changed self.params")
    if _run(strategy, make_view, _asofs()) != before_intents:
        raise _fail(name, "with_params changed what self generates")


def check_with_params_uses_argument(strategy: ParametrizedStrategy, make_view: MakeView) -> None:
    name = "check_with_params_uses_argument"
    for key, value in strategy.params.items():
        changed = {**strategy.params, key: value + 1}
        expected = dict(changed)
        built = strategy.with_params(changed)
        if built is strategy:
            raise _fail(name, "with_params returned self, not a new instance")
        if dict(built.params) != expected:
            raise _fail(name, f"with_params ignored its argument for {key!r}")


def check_params_fresh_and_read_only(strategy: ParametrizedStrategy, make_view: MakeView) -> None:
    name = "check_params_fresh_and_read_only"
    before_params = dict(strategy.params)
    before_intents = _run(strategy, make_view, _asofs())
    first = strategy.params
    if first is strategy.params:
        raise _fail(name, "params returns the same object twice, it must be fresh")
    for key in list(first):
        try:
            first[key] = first[key] + 1  # type: ignore[index]
        except TypeError:
            continue
        raise _fail(name, "params accepted item assignment, it must be read-only")
    mutable_copy = dict(first)
    for key in mutable_copy:
        mutable_copy[key] += 1
    if dict(strategy.params) != before_params:
        raise _fail(name, "mutating what params returned changed the strategy")
    if _run(strategy, make_view, _asofs()) != before_intents:
        raise _fail(name, "mutating what params returned changed the intents")


def check_deterministic(strategy: ParametrizedStrategy, make_view: MakeView) -> None:
    if _run(strategy, make_view, _asofs()) != _run(strategy, make_view, _asofs()):
        raise _fail("check_deterministic", "same inputs gave different intents")


def check_order_independent(strategy: ParametrizedStrategy, make_view: MakeView) -> None:
    forward = _run(strategy, make_view, _asofs())
    backward = _run(strategy, make_view, _asofs()[::-1])[::-1]
    if forward != backward:
        raise _fail("check_order_independent", "reverse date order changed the intents")


def check_short_history_does_not_raise(strategy: ParametrizedStrategy, make_view: MakeView) -> None:
    name = "check_short_history_does_not_raise"
    for sessions in range(strategy.lookback_bars):
        asof = T0 + timedelta(days=sessions - 1)
        try:
            intent = strategy.generate(make_view(asof), asof)
        except Exception as exc:
            raise _fail(name, f"raised {exc!r} with {sessions} sessions of history") from exc
        if not isinstance(intent, Intent):
            raise _fail(name, "did not return an Intent with short history")


def check_lookback_honoured(strategy: ParametrizedStrategy, make_view: MakeView) -> None:
    name = "check_lookback_honoured"
    for sessions in range(strategy.lookback_bars):
        asof = T0 + timedelta(days=sessions - 1)
        try:
            intent = strategy.generate(make_view(asof), asof)
        except Exception:
            continue  # raising is check_short_history_does_not_raise's finding
        if intent.weights:
            raise _fail(name, f"non-empty intent with {sessions} < lookback_bars sessions")


CHECKS: dict[str, Callable[[ParametrizedStrategy, MakeView], None]] = {
    "check_deterministic": check_deterministic,
    "check_params_round_trip": check_params_round_trip,
    "check_with_params_does_not_mutate": check_with_params_does_not_mutate,
    "check_with_params_uses_argument": check_with_params_uses_argument,
    "check_params_fresh_and_read_only": check_params_fresh_and_read_only,
    "check_order_independent": check_order_independent,
    "check_short_history_does_not_raise": check_short_history_does_not_raise,
    "check_lookback_honoured": check_lookback_honoured,
}


def _guarded(name: str, strategy: ParametrizedStrategy, make_view: MakeView) -> None:
    """Run one check; an unexpected exception from the strategy is a failure of that check."""
    try:
        CHECKS[name](strategy, make_view)
    except AssertionError:
        raise
    except Exception as exc:
        raise _fail(name, f"strategy raised {exc!r}") from exc


def check_conformance(strategy: object, make_view: MakeView) -> None:
    check_protocol(strategy)
    assert isinstance(strategy, ParametrizedStrategy)
    for name in CHECKS:
        _guarded(name, strategy, make_view)


def failed_checks(strategy: object, make_view: MakeView) -> set[str]:
    """Names of every check the strategy fails, each run on a fresh strategy when possible."""
    failed: set[str] = set()
    try:
        check_protocol(strategy)
    except AssertionError:
        return {"check_protocol"}
    assert isinstance(strategy, ParametrizedStrategy)
    for name in CHECKS:
        try:
            _guarded(name, strategy, make_view)
        except AssertionError as exc:
            if not str(exc).startswith(name):
                raise AssertionError(f"{name} failed with a foreign message: {exc}") from exc
            failed.add(name)
    return failed


@pytest.fixture
def make_view() -> MakeView:
    provider = InMemoryDataProvider(synthetic_bars(SYMBOLS, start=T0, days=SESSIONS, seed=7))
    return lambda asof: DataView(provider, asof)


# --- deliberately broken strategies ----------------------------------------------------------


class _Good:
    """Reference strategy: baseline logic, written so each subclass breaks exactly one thing."""

    def __init__(self, top_n: int = 2, lookback: int = 5) -> None:
        self._top_n = top_n
        self._lookback = lookback

    @property
    def strategy_id(self) -> str:
        return "toy"

    @property
    def params(self) -> Params:
        return MappingProxyType({"top_n": self._top_n, "lookback": self._lookback})

    @property
    def lookback_bars(self) -> int:
        return self._lookback + 1

    def with_params(self, params: Mapping[str, int | float]) -> "_Good":
        return type(self)(int(params["top_n"]), int(params["lookback"]))

    def generate(self, data: DataView, asof: datetime) -> Intent:
        return BaselineReversal(SYMBOLS, self._top_n, self._lookback).generate(data, asof)


class _Stateful(_Good):
    def __init__(self, top_n: int = 2, lookback: int = 5) -> None:
        super().__init__(top_n, lookback)
        self._seen: set[datetime] = set()

    def generate(self, data: DataView, asof: datetime) -> Intent:
        if asof in self._seen:
            return Intent(asof=asof, weights={})
        self._seen.add(asof)
        return super().generate(data, asof)


class _MutatesArgument(_Good):
    def with_params(self, params: Mapping[str, int | float]) -> "_MutatesArgument":
        params["touched"] = 1  # type: ignore[index]
        return _MutatesArgument(int(params["top_n"]), int(params["lookback"]))


class _IgnoresArgument(_Good):
    def with_params(self, params: Mapping[str, int | float]) -> "_IgnoresArgument":
        return _IgnoresArgument(self._top_n, self._lookback)


class _MutatesSelf(_Good):
    def with_params(self, params: Mapping[str, int | float]) -> "_MutatesSelf":
        self._top_n += 1
        return _MutatesSelf(int(params["top_n"]), int(params["lookback"]))


class _RaisesWhenShort(_Good):
    def generate(self, data: DataView, asof: datetime) -> Intent:
        if not data.bars(SYMBOLS):
            raise RuntimeError("no history")
        return super().generate(data, asof)


class _LiveParams(_Good):
    def __init__(self, top_n: int = 2, lookback: int = 5) -> None:
        super().__init__(top_n, lookback)
        self._live: dict[str, int | float] = {"top_n": top_n, "lookback": lookback}

    @property
    def params(self) -> Params:
        return self._live

    def with_params(self, params: Mapping[str, int | float]) -> "_LiveParams":
        return _LiveParams(int(params["top_n"]), int(params["lookback"]))

    def generate(self, data: DataView, asof: datetime) -> Intent:
        top_n, lookback = int(self._live["top_n"]), int(self._live["lookback"])
        return BaselineReversal(SYMBOLS, top_n, lookback).generate(data, asof)


class _LyingLookback(_Good):
    @property
    def lookback_bars(self) -> int:
        return self._lookback + 1 + 10


class _NotAStrategy:
    strategy_id = "nope"


BROKEN: list[tuple[str, Callable[[], object], str]] = [
    ("stateful", _Stateful, "check_deterministic"),
    ("mutates_argument", _MutatesArgument, "check_with_params_does_not_mutate"),
    ("ignores_argument", _IgnoresArgument, "check_with_params_uses_argument"),
    ("mutates_self", _MutatesSelf, "check_with_params_does_not_mutate"),
    ("raises_when_short", _RaisesWhenShort, "check_short_history_does_not_raise"),
    ("live_params", _LiveParams, "check_params_fresh_and_read_only"),
    ("lying_lookback", _LyingLookback, "check_lookback_honoured"),
    ("not_parametrized", _NotAStrategy, "check_protocol"),
]


def _baseline() -> BaselineReversal:
    return BaselineReversal(SYMBOLS, top_n=2, lookback=5)


def test_reference_toy_passes(make_view: MakeView) -> None:
    check_conformance(_Good(), make_view)


@pytest.mark.parametrize(
    "strategy",
    [_baseline(), BaselineReversal(SYMBOLS, top_n=1, lookback=1)],
    ids=["default", "minimal"],
)
def test_baseline_passes_every_check(strategy: BaselineReversal, make_view: MakeView) -> None:
    check_conformance(strategy, make_view)


def test_baseline_reports_its_params_and_lookback() -> None:
    strategy = _baseline()
    assert dict(strategy.params) == {"top_n": 2, "lookback": 5}
    assert strategy.lookback_bars == 6


@pytest.mark.parametrize(("label", "factory", "intended"), BROKEN, ids=[b[0] for b in BROKEN])
def test_broken_strategy_fails_the_matching_check(
    label: str, factory: Callable[[], object], intended: str, make_view: MakeView
) -> None:
    with pytest.raises(AssertionError, match=intended):
        check_conformance(factory(), make_view)


@pytest.mark.parametrize(("label", "factory", "intended"), BROKEN, ids=[b[0] for b in BROKEN])
def test_broken_strategy_fails_only_the_matching_check(
    label: str, factory: Callable[[], object], intended: str, make_view: MakeView
) -> None:
    assert intended in failed_checks(factory(), make_view)


def test_each_broken_strategy_is_caught_by_exactly_one_check(make_view: MakeView) -> None:
    for label, factory, intended in BROKEN:
        if label == "stateful":
            continue  # state leaks into later checks on the same instance; see failed_checks
        assert failed_checks(factory(), make_view) == {intended}, label


def test_baseline_with_params_validation() -> None:
    strategy = _baseline()
    for bad in ({"top_n": 2}, {"top_n": 2, "lookback": 5, "x": 1}, {"top_n": "a", "lookback": 5}):
        with pytest.raises(ValueError, match=r"param|number|whole|least"):
            strategy.with_params(bad)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match=r"param|number|whole|least"):
        strategy.with_params({"top_n": 0, "lookback": 5})
    with pytest.raises(ValueError, match=r"param|number|whole|least"):
        strategy.with_params({"top_n": 2.5, "lookback": 5})
    with pytest.raises(ValueError, match=r"param|number|whole|least"):
        strategy.with_params({"top_n": True, "lookback": 5})
    rebuilt = strategy.with_params({"top_n": 3.0, "lookback": 4.0})
    assert dict(rebuilt.params) == {"top_n": 3, "lookback": 4}
    assert all(type(v) is int for v in rebuilt.params.values())
    assert dict(strategy.params) == {"top_n": 2, "lookback": 5}
