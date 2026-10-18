import os
import threading
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from contracts.data import Bar, CorporateAction, CorporateActionKind
from harness.data import holdout_store
from harness.data.adjust import find_unadjusted_jumps
from harness.data.calendar import session_close_utc, trading_sessions
from harness.data.holdout_store import (
    MANIFEST_NAME,
    HoldoutAlreadyCarvedError,
    HoldoutManifestError,
    HoldoutPermissionError,
    HoldoutStoreError,
    holdout_provider,
    load_manifest,
    read_holdout,
    verify_holdout_dir,
    write_holdout,
)
from harness.data.split import SplitData, split_data
from harness.data.window import compute_windows
from harness.thresholds import Thresholds
from tests.data_fixtures import series

SESSIONS = trading_sessions(date(2019, 1, 2), date(2021, 12, 31))
LIGHT = Thresholds(holdout_years=1, holdout_embargo_sessions=3, holdout_min_research_years=1)
W = compute_windows(SESSIONS, holdout_years=1, embargo_sessions=3)
SPLIT_DAY = SESSIONS[SESSIONS.index(W.holdout_start) + 10]
NOW = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


def _closes(symbol_shift: float) -> list[float]:
    out = []
    for i, s in enumerate(SESSIONS):
        base = 100.0 + (i % 7) * 0.5 + symbol_shift
        out.append(base / 2 if s >= SPLIT_DAY else base)
    return out


def _bars() -> list[Bar]:
    return [
        *series("AAA", _closes(0.0), start=SESSIONS[0]),
        *series("BBB", _closes(5.0), start=SESSIONS[0]),
    ]


ACTIONS = [
    CorporateAction(
        symbol=sym,
        kind=CorporateActionKind.SPLIT,
        effective_at=datetime(SPLIT_DAY.year, SPLIT_DAY.month, SPLIT_DAY.day, tzinfo=UTC),
        value=2.0,
    )
    for sym in ("AAA", "BBB")
]


@pytest.fixture
def split() -> SplitData:
    return split_data(_bars(), ACTIONS, W, thresholds=LIGHT)


def _carve(d: Path, split: SplitData) -> holdout_store.HoldoutManifest:
    return write_holdout(
        d, split, W, source_snapshot_id="snap1", holdout_years=1, code_version="abc", now=NOW
    )


def _snapshot_files(d: Path) -> dict[str, bytes]:
    return {
        p.relative_to(d).as_posix(): p.read_bytes() for p in sorted(d.rglob("*")) if p.is_file()
    }


def test_round_trip_and_manifest_fields(tmp_path: Path, split: SplitData) -> None:
    d = tmp_path / "hold"
    m = _carve(d, split)
    bars, actions = read_holdout(d)
    expected = sorted([*split.embargo_bars, *split.holdout_bars], key=lambda b: (b.ts, b.symbol))
    assert bars == expected
    assert actions == [*split.embargo_actions, *split.holdout_actions]
    assert m.source_snapshot_id == "snap1"
    assert (m.research_end, m.holdout_start, m.holdout_end) == (
        W.research_end,
        W.holdout_start,
        W.holdout_end,
    )
    assert (m.holdout_years, m.embargo_sessions, m.code_version) == (1, 3, "abc")
    assert m.carved_at == NOW
    assert len(m.content_sha256) == 64
    assert load_manifest(d) == m
    assert (d / MANIFEST_NAME).exists()
    assert (d.stat().st_mode & 0o777) == 0o700


def test_no_temp_leftovers(tmp_path: Path, split: SplitData) -> None:
    _carve(tmp_path / "hold", split)
    assert [p.name for p in tmp_path.iterdir() if p.name != "hold"] == [".hold.carve.lock"]


def test_hash_is_deterministic(tmp_path: Path, split: SplitData) -> None:
    a = _carve(tmp_path / "a", split)
    b = _carve(tmp_path / "b", split)
    assert a.content_sha256 == b.content_sha256
    assert (
        _snapshot_files(tmp_path / "a")["bars/AAA.parquet"]
        == (_snapshot_files(tmp_path / "b")["bars/AAA.parquet"])
    )


def test_recarve_refused_and_bytes_unchanged(tmp_path: Path, split: SplitData) -> None:
    d = tmp_path / "hold"
    _carve(d, split)
    before = _snapshot_files(d)
    with pytest.raises(HoldoutAlreadyCarvedError):
        _carve(d, split)
    assert _snapshot_files(d) == before


def test_crash_before_manifest_then_retry(
    tmp_path: Path, split: SplitData, monkeypatch: pytest.MonkeyPatch
) -> None:
    d = tmp_path / "hold"

    def boom(*_: object) -> None:
        raise RuntimeError("crash")

    with monkeypatch.context() as mp:
        mp.setattr(holdout_store, "_write_manifest", boom)
        with pytest.raises(RuntimeError):
            _carve(d, split)
    assert not (d / MANIFEST_NAME).exists()
    with pytest.raises(HoldoutManifestError):
        verify_holdout_dir(d)
    with pytest.raises(HoldoutManifestError):
        read_holdout(d)
    m = _carve(d, split)
    assert verify_holdout_dir(d) == m


def test_tamper_bars_file(tmp_path: Path, split: SplitData) -> None:
    d = tmp_path / "hold"
    _carve(d, split)
    p = d / "bars" / "AAA.parquet"
    p.write_bytes(p.read_bytes() + b"x")
    with pytest.raises(HoldoutManifestError):
        verify_holdout_dir(d)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("holdout_start", "2021-01-05"),
        ("research_end", "2020-01-02"),
        ("embargo_sessions", 4),
    ],
)
def test_tamper_manifest_fields(
    tmp_path: Path, split: SplitData, field: str, value: object
) -> None:
    import json

    d = tmp_path / "hold"
    _carve(d, split)
    raw = json.loads((d / MANIFEST_NAME).read_text())
    raw[field] = value
    (d / MANIFEST_NAME).write_text(json.dumps(raw))
    with pytest.raises(HoldoutManifestError):
        verify_holdout_dir(d)


def test_tamper_manifest_garbage_or_extra_field(tmp_path: Path, split: SplitData) -> None:
    d = tmp_path / "hold"
    _carve(d, split)
    (d / MANIFEST_NAME).write_text("{not json")
    with pytest.raises(HoldoutManifestError):
        verify_holdout_dir(d)


def test_added_or_removed_file(tmp_path: Path, split: SplitData) -> None:
    d = tmp_path / "hold"
    _carve(d, split)
    (d / "bars" / "ZZZ.parquet").write_bytes((d / "bars" / "AAA.parquet").read_bytes())
    with pytest.raises(HoldoutManifestError):
        verify_holdout_dir(d)
    (d / "bars" / "ZZZ.parquet").unlink()
    verify_holdout_dir(d)
    (d / "notes.txt").write_text("hi")
    with pytest.raises(HoldoutManifestError):
        verify_holdout_dir(d)
    (d / "notes.txt").unlink()
    (d / "bars" / "BBB.parquet").unlink()
    with pytest.raises(HoldoutManifestError):
        verify_holdout_dir(d)


def test_loose_mode_refused_and_700_passes(tmp_path: Path, split: SplitData) -> None:
    d = tmp_path / "hold"
    _carve(d, split)
    os.chmod(d, 0o755)
    with pytest.raises(HoldoutPermissionError, match="chmod 700"):
        verify_holdout_dir(d)
    with pytest.raises(HoldoutPermissionError):
        read_holdout(d)
    os.chmod(d, 0o750)
    with pytest.raises(HoldoutPermissionError):
        verify_holdout_dir(d)
    os.chmod(d, 0o700)
    verify_holdout_dir(d)


def test_existing_loose_dir_is_tightened(tmp_path: Path, split: SplitData) -> None:
    d = tmp_path / "hold"
    d.mkdir()
    os.chmod(d, 0o755)
    _carve(d, split)
    assert (d.stat().st_mode & 0o777) == 0o700


def test_unknown_files_refused(tmp_path: Path, split: SplitData) -> None:
    d = tmp_path / "hold"
    d.mkdir()
    (d / "precious.txt").write_text("keep")
    with pytest.raises(HoldoutStoreError):
        _carve(d, split)
    assert (d / "precious.txt").read_text() == "keep"
    assert not (d / MANIFEST_NAME).exists()


def test_ledger_allowed_and_not_hashed(tmp_path: Path, split: SplitData) -> None:
    d = tmp_path / "hold"
    d.mkdir()
    (d / "holdout_ledger.jsonl").write_text("{}\n")
    m = _carve(d, split)
    (d / "holdout_ledger.jsonl").write_text("{}\n{}\n")
    assert verify_holdout_dir(d) == m
    other = _carve(tmp_path / "other", split)
    assert other.content_sha256 == m.content_sha256


def test_two_writers_exactly_one_succeeds(tmp_path: Path, split: SplitData) -> None:
    d = tmp_path / "hold"
    results: list[str] = []
    barrier = threading.Barrier(2)

    def run() -> None:
        barrier.wait()
        try:
            _carve(d, split)
            results.append("ok")
        except HoldoutAlreadyCarvedError:
            results.append("refused")

    threads = [threading.Thread(target=run) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(results) == ["ok", "refused"]
    verify_holdout_dir(d)


def test_provider_split_in_holdout_is_not_a_crash(tmp_path: Path, split: SplitData) -> None:
    d = tmp_path / "hold"
    _carve(d, split)
    end = session_close_utc(W.holdout_end)
    prov = holdout_provider(split.research_bars, split.research_actions, d)
    bars = prov.bars(["AAA", "BBB"], end)
    assert find_unadjusted_jumps(bars) == []
    # Adjusted on one basis: the last research bar and the first holdout bar are comparable.
    aaa = [b for b in bars if b.symbol == "AAA"]
    assert max(b.close for b in aaa) < 110.0
    assert min(b.close for b in aaa) > 40.0
    assert len(aaa) == len(SESSIONS)


def test_research_only_reads_never_apply_holdout_split(split: SplitData) -> None:
    from harness.data.provider import ComposedDataProvider

    prov = ComposedDataProvider(split.research_bars, corporate_actions=split.research_actions)
    end = session_close_utc(W.research_end)
    raw = {(b.symbol, b.ts): b.close for b in split.research_bars}
    got = prov.bars(["AAA", "BBB"], end)
    assert all(raw[(b.symbol, b.ts)] == b.close for b in got)
    assert prov.corporate_actions(["AAA", "BBB"], end) == []
