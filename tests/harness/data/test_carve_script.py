from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from contracts.data import Bar, CorporateAction
from harness.data.cache import ActionsStore, ParquetCache
from harness.data.calendar import session_close_utc
from harness.data.common import SpinOff
from harness.data.holdout_store import MANIFEST_NAME, load_manifest, verify_holdout_dir
from harness.data.reconcile import ReconcileConfig, ReconciliationReport
from harness.data.window import compute_windows
from harness.thresholds import DEFAULT_THRESHOLDS, Thresholds
from scripts import carve_holdout
from scripts.carve_holdout import carve
from tests.data_fixtures import series

LIGHT = Thresholds(holdout_years=1, holdout_embargo_sessions=3, holdout_min_research_years=1)
START = date(2024, 1, 2)


def _closes(n: int, base: float) -> list[float]:
    return [base + 0.01 * i + (i % 3) * 0.3 for i in range(n)]


def _bars(n: int = 540, start: date = START) -> list[Bar]:
    return [
        *series("AAA", _closes(n, 100.0), start=start),
        *series("BBB", _closes(n, 47.0), start=start),
    ]


def _now_for(bars: Sequence[Bar], days_after: int = 1) -> datetime:
    last = max(b.ts for b in bars)
    return last + timedelta(days=days_after)


class _Checker:
    def __init__(self, ok: bool) -> None:
        self.name = "fake"
        self._ok = ok

    def check(
        self, primary: Sequence[Bar], symbols: Sequence[str], config: ReconcileConfig
    ) -> ReconciliationReport:
        return ReconciliationReport(
            source="fake",
            compared=len(primary),
            primary_total=len(primary),
            ok=self._ok,
            reason="" if self._ok else "fake disagreement",
        )


class _Fetch:
    def __init__(
        self,
        bars: list[Bar],
        actions: list[CorporateAction] | None = None,
        spin_offs: list[SpinOff] | None = None,
    ) -> None:
        self.bars = bars
        self.actions = actions or []
        self.spin_offs = spin_offs or []
        self.calls = 0

    def fetch_bars(self, symbols: Sequence[str], start: datetime, end: datetime) -> list[Bar]:
        self.calls += 1
        return list(self.bars)

    def fetch_actions(
        self, symbols: Sequence[str], start: datetime, end: datetime
    ) -> list[CorporateAction]:
        self.calls += 1
        return list(self.actions)

    def fetch_spin_offs(
        self, symbols: Sequence[str], start: datetime, end: datetime
    ) -> list[SpinOff]:
        self.calls += 1
        return list(self.spin_offs)


def _run(
    tmp_path: Path,
    bars: list[Bar],
    *,
    now: datetime | None = None,
    checkers: Sequence[_Checker] = (),
    thresholds: Thresholds = LIGHT,
    fetch: _Fetch | None = None,
    **flags: bool,
) -> tuple[int, _Fetch, list[str]]:
    fetch = fetch or _Fetch(bars)
    lines: list[str] = []
    code = carve(
        symbols=["AAA", "BBB"],
        years=10,
        holdout_dir=tmp_path / "hold",
        cache_dir=tmp_path / "cache",
        fetch_bars=fetch.fetch_bars,
        fetch_actions=fetch.fetch_actions,
        fetch_spin_offs=fetch.fetch_spin_offs,
        cross_checkers=list(checkers),
        now=now or _now_for(bars),
        thresholds=thresholds,
        code_version="test",
        out=lines.append,
        **flags,
    )
    return code, fetch, lines


def _research_end(bars: list[Bar], thresholds: Thresholds = LIGHT) -> date:
    sessions = sorted({b.ts.date() for b in bars})
    return compute_windows(
        sessions, thresholds.holdout_years, thresholds.holdout_embargo_sessions
    ).research_end


def _cache_bars(cache: Path) -> list[Bar]:
    pc = ParquetCache(cache)
    return [b for p in sorted((cache / "bars").glob("*.parquet")) for b in pc.load(p.stem)]


def test_happy_path_writes_holdout_and_capped_research_cache(tmp_path: Path) -> None:
    bars = _bars()
    code, _, lines = _run(tmp_path, bars, checkers=[_Checker(True)])
    assert code == 0
    hold = tmp_path / "hold"
    manifest = verify_holdout_dir(hold)
    assert manifest.code_version == "test"
    assert manifest.source_snapshot_id
    research_end = _research_end(bars)
    cached = _cache_bars(tmp_path / "cache")
    assert cached
    assert max(b.ts.date() for b in cached) == research_end
    assert all(b.ts <= session_close_utc(research_end) for b in cached)
    assert str(hold / MANIFEST_NAME) in lines[-1] or any(
        str(hold / MANIFEST_NAME) in x for x in lines
    )
    assert any("frozen" in x and "re-carv" in x.lower() for x in lines)


def test_research_cache_never_gets_actions_after_research_end(tmp_path: Path) -> None:
    bars = _bars()
    sessions = sorted({b.ts.date() for b in bars})
    late = sessions[-5]
    early = sessions[100]
    from contracts.data import CorporateActionKind

    actions = [
        CorporateAction(
            symbol="AAA",
            kind=CorporateActionKind.DIVIDEND,
            value=0.1,
            effective_at=datetime(d.year, d.month, d.day, tzinfo=UTC),
        )
        for d in (early, late)
    ]
    code, _, _ = _run(tmp_path, bars, fetch=_Fetch(bars, actions))
    assert code == 0
    stored = ActionsStore(tmp_path / "cache").load()
    assert [a.effective_at.date() for a in stored] == [early]


def test_default_thresholds_on_big_dataset(tmp_path: Path) -> None:
    bars = _bars(n=1300, start=date(2021, 1, 4))
    code, _, _ = _run(tmp_path, bars, thresholds=DEFAULT_THRESHOLDS)
    assert code == 0
    m = load_manifest(tmp_path / "hold")
    assert (m.holdout_years, m.embargo_sessions) == (2, 10)


def test_dry_run_writes_nothing_and_creates_no_directories(tmp_path: Path) -> None:
    bars = _bars()
    code, fetch, lines = _run(tmp_path, bars, dry_run=True)
    assert code == 0
    assert fetch.calls > 0
    assert list(tmp_path.iterdir()) == []
    assert any("holdout" in x.lower() for x in lines)


def test_rerun_refuses_without_any_fetch(tmp_path: Path) -> None:
    bars = _bars()
    assert _run(tmp_path, bars)[0] == 0
    before = {p: p.read_bytes() for p in (tmp_path / "hold").rglob("*") if p.is_file()}
    code, fetch, lines = _run(tmp_path, bars)
    assert code == 1
    assert fetch.calls == 0
    assert any("already carved" in x.lower() for x in lines)
    after = {p: p.read_bytes() for p in (tmp_path / "hold").rglob("*") if p.is_file()}
    assert before == after


def _spin(symbol: str, ex_date: date) -> SpinOff:
    return SpinOff(source_symbol=symbol, ex_date=ex_date, new_symbol="NEWCO", new_rate=0.25)


def _tree(root: Path) -> dict[Path, bytes]:
    return {p: p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


@pytest.mark.parametrize("dry_run", [False, True])
def test_spin_off_inside_window_refuses_and_writes_nothing(tmp_path: Path, dry_run: bool) -> None:
    bars = _bars()
    cache = tmp_path / "cache"
    ParquetCache(cache).store("OLD", series("OLD", _closes(30, 20.0), start=START))
    before = _tree(tmp_path)
    ex = sorted({b.ts.date() for b in bars})[200]
    fetch = _Fetch(bars, spin_offs=[_spin("BBB", ex)])
    code, _, lines = _run(tmp_path, bars, fetch=fetch, dry_run=dry_run)
    assert code == 1
    assert any("spin_off (BBB)" in x for x in lines)
    assert _tree(tmp_path) == before
    assert not (tmp_path / "hold").exists()


def test_spin_off_outside_universe_or_window_is_ignored(tmp_path: Path) -> None:
    bars = _bars()
    ex = sorted({b.ts.date() for b in bars})[200]
    spins = [_spin("ZZZ", ex), _spin("AAA", START - timedelta(days=400))]
    code, _, _ = _run(tmp_path, bars, fetch=_Fetch(bars, spin_offs=spins))
    assert code == 0


def test_reconciliation_failure_aborts_before_any_write(tmp_path: Path) -> None:
    code, _, lines = _run(tmp_path, _bars(), checkers=[_Checker(False)])
    assert code == 1
    assert list(tmp_path.iterdir()) == []
    assert any("fake disagreement" in x for x in lines)


def test_quality_failure_aborts_before_any_write(tmp_path: Path) -> None:
    bars = _bars()
    gapped = [b for b in bars if not (b.symbol == "AAA" and b.ts.date() in _gap_days(bars))]
    code, _, _ = _run(tmp_path, gapped, now=_now_for(bars))
    assert code == 1
    assert list(tmp_path.iterdir()) == []


def _gap_days(bars: list[Bar]) -> set[date]:
    days = sorted({b.ts.date() for b in bars})
    return {days[200], days[201]}


def test_stale_last_session_refused_unless_allowed(tmp_path: Path) -> None:
    bars = _bars()
    stale_now = _now_for(bars, days_after=30)
    code, _, lines = _run(tmp_path, bars, now=stale_now)
    assert code == 1
    assert any("stale" in x.lower() for x in lines)
    assert list(tmp_path.iterdir()) == []
    code, _, _ = _run(tmp_path, bars, now=stale_now, allow_stale=True)
    assert code == 0


def test_stale_cache_refused_then_prune_fixes_it(tmp_path: Path) -> None:
    bars = _bars()
    research_end = _research_end(bars)
    cache = tmp_path / "cache"
    old = series("OLD", _closes(540, 20.0), start=START)
    ParquetCache(cache).store("OLD", old)
    pre = [b for b in old if b.ts.date() <= research_end]
    assert len(pre) < len(old)

    code, _, lines = _run(tmp_path, bars)
    assert code == 1
    assert any("prune-cache" in x for x in lines)
    assert not (tmp_path / "hold").exists()
    assert ParquetCache(cache).load("OLD") == old

    code, _, _ = _run(tmp_path, bars, prune_cache=True)
    assert code == 0
    assert ParquetCache(cache).load("OLD") == pre
    assert max(b.ts.date() for b in _cache_bars(cache)) == research_end


def test_prune_cache_also_prunes_stale_actions(tmp_path: Path) -> None:
    from contracts.data import CorporateActionKind

    bars = _bars()
    sessions = sorted({b.ts.date() for b in bars})
    late = sessions[-3]
    ActionsStore(tmp_path / "cache").store(
        [
            CorporateAction(
                symbol="OLD",
                kind=CorporateActionKind.DIVIDEND,
                value=0.2,
                effective_at=datetime(late.year, late.month, late.day, tzinfo=UTC),
            )
        ]
    )
    assert _run(tmp_path, bars)[0] == 1
    assert _run(tmp_path, bars, prune_cache=True)[0] == 0
    assert ActionsStore(tmp_path / "cache").load() == []


def test_split_floor_refusal_is_exit_1(tmp_path: Path) -> None:
    bars = _bars(n=300)
    code, _, lines = _run(tmp_path, bars)
    assert code == 1
    assert lines
    assert list(tmp_path.iterdir()) == []


def test_output_has_dates_but_no_bar_prices(tmp_path: Path) -> None:
    bars = _bars()
    code, _, lines = _run(tmp_path, bars, dry_run=True)
    assert code == 0
    text = "\n".join(lines)
    w = compute_windows(sorted({b.ts.date() for b in bars}), 1, 3)
    for d in (w.research_end, w.holdout_start, w.holdout_end):
        assert d.isoformat() in text
    for b in bars:
        assert repr(b.close) not in text
        assert f"{b.close:.2f}" not in text


def test_main_missing_env_exits_2(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    for var in ("FIN_RESEARCH_HOLDOUT_DIR", "LAB_DATA_CACHE_DIR", "ALPACA_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    assert carve_holdout.main(["--symbols", "AAA"]) == 2
    assert capsys.readouterr().out


def test_main_requires_symbols() -> None:
    with pytest.raises(SystemExit) as e:
        carve_holdout.main([])
    assert e.value.code == 2
