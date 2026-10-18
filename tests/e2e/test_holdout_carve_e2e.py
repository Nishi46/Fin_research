"""Holdout carve end to end on a synthetic snapshot: carve, research pipeline, gate, leak scan.

Nothing is mocked except the network (fetchers are injected). The bars are seeded random walks
on real NYSE sessions. Three worlds share the same shape and differ only in where the planted
edge on the EDGE symbol sits: inside the holdout, only in the embargo and warm-up sessions, or
nowhere (noise).
"""

import os
import shutil
import tempfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import numpy as np
import pandas as pd  # type: ignore[import-untyped]
import pytest

from contracts.capital import CapitalConfig
from contracts.data import Bar, CorporateAction, DataProvider, Snapshot
from contracts.dataview import DataView
from contracts.intent import Intent
from contracts.registry import Outcome
from harness.api import BacktestResult
from harness.data.cache import ActionsStore, ParquetCache
from harness.data.calendar import session_close_utc
from harness.data.holdout_store import (
    MANIFEST_NAME,
    HoldoutManifest,
    holdout_provider,
    load_manifest,
    read_holdout,
)
from harness.data.provider import ComposedDataProvider
from harness.data.window import ResearchWindow, compute_windows
from harness.engine.runner import run_backtest
from harness.registry.sqlite import SqliteRegistry
from harness.thresholds import Thresholds
from harness.validation.holdout import (
    HOLDOUT_ENV_VAR,
    HoldoutGate,
    HoldoutLocationError,
    resolve_holdout_dir,
)
from harness.validation.pipeline import run_pipeline
from scripts.carve_holdout import carve
from strategies.baseline import BaselineReversal
from tests.data_fixtures import series

REPO_ROOT = Path(__file__).resolve().parents[2]
LIGHT = Thresholds(holdout_years=1, holdout_embargo_sessions=3, holdout_min_research_years=1)
START = date(2024, 1, 2)
N_SESSIONS = 540
SYMBOLS = ["AAA", "EDGE"]
NOISE_SD = 0.004
DRIFT = 0.004
WARMUP_SESSIONS = 120
WARMUP_DRIFT = 0.015  # strong enough that scoring the warm-up would pass the gate
SEED = 7
_SKIP_DIRS = {".git", ".venv", ".worktrees", "__pycache__", "node_modules"}
_CACHE_DIRS = {".mypy_cache", ".ruff_cache", ".pytest_cache", ".hypothesis"}
_MAX_SCAN_BYTES = 5_000_000


class LongEdge:
    """Always fully long EDGE; its profit is whatever EDGE did."""

    def __init__(self, strategy_id: str) -> None:
        self.strategy_id = strategy_id

    def generate(self, data: DataView, asof: datetime) -> Intent:
        present = {b.symbol for b in data.bars(["EDGE"])}
        return Intent(asof=asof, weights={"EDGE": 1.0} if present else {})


class Recorder:
    """Pass-through DataProvider that remembers the newest bar or action it ever returned."""

    def __init__(self, inner: DataProvider) -> None:
        self._inner = inner
        self.newest: datetime | None = None
        self.calls = 0

    def _see(self, stamps: Sequence[datetime]) -> None:
        self.calls += 1
        if stamps:
            top = max(stamps)
            self.newest = top if self.newest is None else max(self.newest, top)

    def snapshot(self) -> Snapshot:
        return self._inner.snapshot()

    def bars(self, symbols: Sequence[str], end: datetime) -> list[Bar]:
        out = self._inner.bars(symbols, end)
        self._see([b.ts for b in out])
        return out

    def corporate_actions(self, symbols: Sequence[str], end: datetime) -> list[CorporateAction]:
        out = self._inner.corporate_actions(symbols, end)
        self._see([a.effective_at for a in out])
        return out

    def membership(self, index: str, on: date) -> frozenset[str]:
        return self._inner.membership(index, on)


@dataclass(frozen=True)
class World:
    hold: Path
    cache: Path
    source_bars: list[Bar]
    manifest: HoldoutManifest
    code: int
    lines: list[str]

    @property
    def research_end(self) -> date:
        return self.manifest.research_end

    def research_bars(self) -> list[Bar]:
        pc = ParquetCache(self.cache)
        return [b for s in SYMBOLS for b in pc.load(s)]

    def research_actions(self) -> list[CorporateAction]:
        return ActionsStore(self.cache).load()

    def window(self) -> tuple[datetime, datetime]:
        stamps = sorted({b.ts for b in self.source_bars})
        return stamps[0], stamps[-1]


def _capital(world: World) -> CapitalConfig:
    return CapitalConfig(amount=Decimal("10000"), effective_at=world.window()[0], source="default")


def _sessions() -> list[date]:
    return [
        b.ts.date() for b in series("X", [1.0 + 0.001 * i for i in range(N_SESSIONS)], start=START)
    ]


def _edge_closes(kind: str, sessions: list[date]) -> list[float]:
    w = compute_windows(sessions, LIGHT.holdout_years, LIGHT.holdout_embargo_sessions)
    rng = np.random.default_rng(SEED)
    noise = rng.standard_normal(len(sessions)) * NOISE_SD
    warm_start = sessions.index(w.research_end) - WARMUP_SESSIONS
    steps = []
    for i, d in enumerate(sessions):
        in_holdout = kind == "holdout" and d >= w.holdout_start
        in_embargo = kind == "embargo" and warm_start < i and d < w.holdout_start
        drift = DRIFT if in_holdout else WARMUP_DRIFT if in_embargo else 0.0
        steps.append(noise[i] + drift)
    return [float(c) for c in 100.0 * np.exp(np.cumsum(steps))]


def _aaa_closes(n: int) -> list[float]:
    return [100.0 + 0.01 * i + (i % 3) * 0.3 for i in range(n)]


def _source_bars(kind: str) -> list[Bar]:
    sessions = _sessions()
    return [
        *series("AAA", _aaa_closes(N_SESSIONS), start=START),
        *series("EDGE", _edge_closes(kind, sessions), start=START),
    ]


def _carve(root: Path, bars: list[Bar]) -> tuple[int, list[str]]:
    lines: list[str] = []
    code = carve(
        symbols=SYMBOLS,
        years=10,
        holdout_dir=root / "hold",
        cache_dir=root / "cache",
        fetch_bars=lambda symbols, start, end: list(bars),
        fetch_actions=lambda symbols, start, end: [],
        cross_checkers=[],
        now=max(b.ts for b in bars) + timedelta(days=1),
        thresholds=LIGHT,
        code_version="e2e",
        out=lines.append,
    )
    return code, lines


def _make_world(root: Path, kind: str) -> World:
    bars = _source_bars(kind)
    code, lines = _carve(root, bars)
    manifest = load_manifest(root / "hold") if code == 0 else None
    assert manifest is not None
    return World(root / "hold", root / "cache", bars, manifest, code, lines)


@pytest.fixture(scope="module")
def worlds(tmp_path_factory: pytest.TempPathFactory) -> dict[str, World]:
    return {k: _make_world(tmp_path_factory.mktemp(k), k) for k in ("holdout", "embargo", "noise")}


def _gate(world: World) -> HoldoutGate:
    return HoldoutGate(environ={HOLDOUT_ENV_VAR: str(world.hold)})


def _verdict(world: World, strategy_id: str) -> bool:
    strategy = LongEdge(strategy_id)
    start, end = world.window()
    bars, actions = world.research_bars(), world.research_actions()

    def run(directory: Path) -> BacktestResult:
        return run_backtest(
            provider=holdout_provider(bars, actions, directory),
            strategy=strategy,
            symbols=SYMBOLS,
            start=start,
            end=end,
            capital=_capital(world),
            seed=SEED,
            code_version="e2e",
        )

    return _gate(world).evaluate(strategy_id, "hash-" + strategy_id, run).passed


def _research_pipeline(provider: DataProvider, world: World) -> tuple[Outcome, str]:
    _, end = world.window()
    registry = SqliteRegistry()
    result = run_pipeline(
        strategy=BaselineReversal(symbols=SYMBOLS, top_n=1, lookback=5),
        build=lambda p: BaselineReversal(symbols=SYMBOLS, top_n=1, lookback=int(p["lookback"])),
        params={"lookback": 5},
        provider=provider,
        symbols=SYMBOLS,
        start=world.window()[0],
        end=end,
        capital=_capital(world),
        registry=registry,
        seed=SEED,
    )
    return result.outcome, registry.get(result.trial.id).data_snapshot_id


# --- 1. the carve -------------------------------------------------------------------------


def test_carve_completes_and_freezes_the_windows(worlds: dict[str, World]) -> None:
    w = worlds["holdout"]
    assert w.code == 0
    assert (w.hold / MANIFEST_NAME).is_file()
    assert (w.hold.stat().st_mode & 0o777) == 0o700
    last_session = max(b.ts.date() for b in w.source_bars)
    assert w.manifest.holdout_end == last_session
    assert w.manifest.research_end < w.manifest.holdout_start
    cached = w.research_bars()
    assert max(b.ts for b in cached) == session_close_utc(w.research_end)


# --- 2. research path ---------------------------------------------------------------------


def test_research_pipeline_never_sees_a_bar_after_research_end(worlds: dict[str, World]) -> None:
    w = worlds["holdout"]
    cap = session_close_utc(w.research_end)
    composed = ComposedDataProvider(w.research_bars(), corporate_actions=w.research_actions())
    spy = Recorder(ResearchWindow(composed, w.research_end))

    outcome, snapshot_id = _research_pipeline(spy, w)

    assert outcome != Outcome.ERROR
    assert spy.calls > 0
    assert spy.newest == cap, "the pipeline read right up to research_end and not past it"
    assert snapshot_id == composed.snapshot().id
    assert composed.snapshot().end <= cap


def test_research_window_clamps_even_when_the_inner_provider_holds_everything(
    worlds: dict[str, World],
) -> None:
    w = worlds["holdout"]
    cap = session_close_utc(w.research_end)
    everything = ComposedDataProvider(w.source_bars)
    spy = Recorder(ResearchWindow(everything, w.research_end))

    outcome, _ = _research_pipeline(spy, w)

    assert outcome != Outcome.ERROR
    assert spy.newest is not None
    assert spy.newest <= cap
    assert ResearchWindow(everything, w.research_end).snapshot().end <= cap


# --- 3. gate path -------------------------------------------------------------------------


def test_gate_passes_an_edge_planted_inside_the_holdout(worlds: dict[str, World]) -> None:
    assert _verdict(worlds["holdout"], "edge_in_holdout") is True


def test_gate_fails_noise(worlds: dict[str, World]) -> None:
    assert _verdict(worlds["noise"], "noise_only") is False


def test_gate_ignores_an_edge_confined_to_embargo_and_warmup(worlds: dict[str, World]) -> None:
    w = worlds["embargo"]
    bars, _ = read_holdout(w.hold)
    first_held = min(b.ts.date() for b in bars)
    assert first_held <= w.manifest.holdout_start - timedelta(days=1), (
        "the holdout directory really holds embargo sessions the backtest will trade through"
    )
    assert _verdict(w, "edge_in_embargo") is False


# --- 4. repo leak scan --------------------------------------------------------------------


def _repo_files() -> list[Path]:
    found: list[Path] = []
    for root, dirs, files in os.walk(REPO_ROOT):
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS | _CACHE_DIRS]
        found.extend(Path(root) / f for f in files)
    return found


def test_no_holdout_bar_exists_anywhere_under_the_repo(worlds: dict[str, World]) -> None:
    w = worlds["holdout"]
    assert REPO_ROOT not in w.hold.resolve().parents
    assert REPO_ROOT not in w.cache.resolve().parents

    held = [b for b in w.source_bars if b.ts.date() >= w.manifest.holdout_start]
    assert held
    held_keys = {(b.symbol, b.ts) for b in held}
    held_tokens = {repr(b.close) for b in held if b.symbol == "EDGE"}

    for path in _repo_files():
        if path.suffix == ".parquet":
            frame = pd.read_parquet(path)
            if {"symbol", "ts"} <= set(frame.columns):
                got = {
                    (str(r.symbol), pd.Timestamp(r.ts).to_pydatetime()) for r in frame.itertuples()
                }
                assert not got & held_keys, f"holdout bar found in {path}"
            continue
        if path.suffix in {".json", ".csv"} and path.stat().st_size <= _MAX_SCAN_BYTES:
            text = path.read_text(errors="ignore")
            assert not any(t in text for t in held_tokens), f"holdout close found in {path}"


def test_gate_location_check_refuses_a_holdout_inside_the_repo() -> None:
    inside = Path(tempfile.mkdtemp(dir=REPO_ROOT, prefix=".holdout_e2e_"))
    try:
        with pytest.raises(HoldoutLocationError):
            resolve_holdout_dir({HOLDOUT_ENV_VAR: str(inside)})
    finally:
        shutil.rmtree(inside, ignore_errors=True)
    assert not inside.exists()


# --- 5. re-carve and the window leak ------------------------------------------------------


def _snapshot_files(*roots: Path) -> dict[Path, bytes]:
    return {p: p.read_bytes() for r in roots for p in sorted(r.rglob("*")) if p.is_file()}


def test_recarve_is_refused_and_leaves_every_byte_alone(worlds: dict[str, World]) -> None:
    w = worlds["holdout"]
    before = _snapshot_files(w.hold, w.cache)
    code, lines = _carve(w.hold.parent, w.source_bars)
    assert code == 1
    assert any("already carved" in x.lower() for x in lines)
    assert _snapshot_files(w.hold, w.cache) == before


@pytest.mark.parametrize("make_inner", ["capped", "everything"])
def test_asking_for_the_end_of_the_holdout_returns_nothing_newer_than_research_end(
    worlds: dict[str, World], make_inner: str
) -> None:
    w = worlds["holdout"]
    cap = session_close_utc(w.research_end)
    builders: dict[str, Callable[[], ComposedDataProvider]] = {
        "capped": lambda: ComposedDataProvider(
            w.research_bars(), corporate_actions=w.research_actions()
        ),
        "everything": lambda: ComposedDataProvider(w.source_bars),
    }
    wrapped = ResearchWindow(builders[make_inner](), w.research_end)
    holdout_end = datetime.combine(w.manifest.holdout_end, datetime.max.time(), tzinfo=UTC)

    bars = wrapped.bars(SYMBOLS, holdout_end)
    assert bars
    assert max(b.ts for b in bars) == cap
    assert all(a.effective_at <= cap for a in wrapped.corporate_actions(SYMBOLS, holdout_end))
