"""On-disk store for the embargo + holdout slice, with a tamper-evident manifest.

Layout under the holdout directory: `bars/{SYMBOL}.parquet`, `actions.parquet` (same formats as
`ParquetCache` / `ActionsStore`), `holdout_manifest.json` (written last) and, owned by the gate,
`holdout_ledger.jsonl` (never hashed: it changes on every query).

`content_sha256` hashes the parquet *bytes* (pyarrow output is byte-stable for identical input;
a test pins this) in sorted-path order, preceded by a header of the manifest's carve parameters
so editing a date or the embargo length in the manifest is detected as well as editing data.
"""

import fcntl
import hashlib
import json
import os
import shutil
import stat
import tempfile
from collections import defaultdict
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from datetime import UTC, date, datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict, ValidationError, field_validator

from contracts.data import Bar, CorporateAction
from harness.data.cache import ActionsStore, ParquetCache
from harness.data.provider import ComposedDataProvider
from harness.data.split import SplitData
from harness.data.window import Windows

MANIFEST_NAME = "holdout_manifest.json"
_LEDGER_NAME = "holdout_ledger.jsonl"  # the gate's file; kept in sync with validation.holdout
_ACTIONS_NAME = "actions.parquet"
_BARS_DIR = "bars"
_DIR_MODE = 0o700
_GROUP_OTHER_BITS = 0o077


class HoldoutStoreError(Exception):
    pass


class HoldoutAlreadyCarvedError(HoldoutStoreError):
    pass


class HoldoutManifestError(HoldoutStoreError):
    pass


class HoldoutPermissionError(HoldoutStoreError):
    pass


class HoldoutManifest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    source_snapshot_id: str
    research_end: date
    holdout_start: date
    holdout_end: date
    holdout_years: int
    embargo_sessions: int
    content_sha256: str
    carved_at: datetime
    code_version: str

    @field_validator("carved_at")
    @classmethod
    def _utc(cls, v: datetime) -> datetime:
        if v.tzinfo is None or v.utcoffset() != UTC.utcoffset(None):
            raise ValueError("carved_at must be timezone-aware UTC")
        return v


def _data_files(directory: Path) -> list[Path]:
    """Every file that belongs to the hashed payload; refuses anything unexpected."""
    found: list[Path] = []
    for path in sorted(directory.rglob("*")):
        if path.is_dir():
            if path != directory / _BARS_DIR:
                raise HoldoutManifestError(f"unexpected directory in holdout store: {path.name}")
            continue
        rel = path.relative_to(directory)
        if rel.as_posix() in (MANIFEST_NAME, _LEDGER_NAME):
            continue
        if rel.as_posix() == _ACTIONS_NAME or (
            rel.parent.as_posix() == _BARS_DIR and rel.suffix == ".parquet"
        ):
            found.append(path)
            continue
        raise HoldoutManifestError(f"unexpected file in holdout store: {rel.as_posix()}")
    return found


def _content_hash(
    directory: Path,
    *,
    research_end: date,
    holdout_start: date,
    holdout_end: date,
    holdout_years: int,
    embargo_sessions: int,
    source_snapshot_id: str,
) -> str:
    h = hashlib.sha256()
    header = json.dumps(
        [
            source_snapshot_id,
            research_end.isoformat(),
            holdout_start.isoformat(),
            holdout_end.isoformat(),
            holdout_years,
            embargo_sessions,
        ]
    ).encode()
    h.update(len(header).to_bytes(8, "big") + header)
    for path in _data_files(directory):
        rel = path.relative_to(directory).as_posix().encode()
        data = path.read_bytes()
        h.update(len(rel).to_bytes(8, "big") + rel + len(data).to_bytes(8, "big") + data)
    return h.hexdigest()


def _hash_for(directory: Path, m: HoldoutManifest) -> str:
    return _content_hash(
        directory,
        research_end=m.research_end,
        holdout_start=m.holdout_start,
        holdout_end=m.holdout_end,
        holdout_years=m.holdout_years,
        embargo_sessions=m.embargo_sessions,
        source_snapshot_id=m.source_snapshot_id,
    )


def _write_manifest(directory: Path, manifest: HoldoutManifest) -> None:
    """Complete content or nothing: write a temp file, then hard-link it into place, which
    fails atomically if a manifest already exists."""
    final = directory / MANIFEST_NAME
    tmp = directory / f".{MANIFEST_NAME}.{os.getpid()}.tmp"
    with open(tmp, "x") as f:
        f.write(manifest.model_dump_json(indent=2))
        f.flush()
        os.fsync(f.fileno())
    try:
        os.link(tmp, final)
    except FileExistsError as e:
        raise HoldoutAlreadyCarvedError(f"holdout already carved: {final}") from e
    finally:
        tmp.unlink(missing_ok=True)


@contextmanager
def _carve_lock(directory: Path) -> Iterator[None]:
    lock_path = directory.parent / f".{directory.name}.carve.lock"
    with open(lock_path, "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def _prepare_target(directory: Path) -> None:
    """Allow only an empty dir, the gate's ledger, or leftovers of an unfinished carve."""
    for child in directory.iterdir():
        if child.name == _LEDGER_NAME and child.is_file():
            continue
        if child.name == _ACTIONS_NAME and child.is_file():
            continue
        if child.name == _BARS_DIR and child.is_dir():
            if any(not (p.is_file() and p.suffix == ".parquet") for p in child.iterdir()):
                raise HoldoutStoreError(f"refusing to overwrite unknown files in {child}")
            continue
        raise HoldoutStoreError(f"refusing to overwrite unknown file: {child.name}")
    shutil.rmtree(directory / _BARS_DIR, ignore_errors=True)
    (directory / _ACTIONS_NAME).unlink(missing_ok=True)


def write_holdout(
    directory: Path,
    split: SplitData,
    windows: Windows,
    *,
    source_snapshot_id: str,
    holdout_years: int,
    code_version: str,
    now: datetime | None = None,
) -> HoldoutManifest:
    bars = [*split.embargo_bars, *split.holdout_bars]
    actions = [*split.embargo_actions, *split.holdout_actions]
    carved_at = (now or datetime.now(UTC)).astimezone(UTC)
    directory.parent.mkdir(parents=True, exist_ok=True)
    with _carve_lock(directory):
        if (directory / MANIFEST_NAME).exists():
            raise HoldoutAlreadyCarvedError(f"holdout already carved: {directory}")
        directory.mkdir(mode=_DIR_MODE, exist_ok=True)
        directory.chmod(_DIR_MODE)
        _prepare_target(directory)

        tmp = Path(tempfile.mkdtemp(dir=directory.parent, prefix=f".{directory.name}.tmp-"))
        try:
            by_symbol: dict[str, list[Bar]] = defaultdict(list)
            for b in bars:
                by_symbol[b.symbol].append(b)
            cache = ParquetCache(tmp)
            for symbol, symbol_bars in by_symbol.items():
                cache.store(symbol, symbol_bars)
            ActionsStore(tmp).store(actions)

            params = {
                "research_end": windows.research_end,
                "holdout_start": windows.holdout_start,
                "holdout_end": windows.holdout_end,
                "holdout_years": holdout_years,
                "embargo_sessions": windows.embargo_sessions,
                "source_snapshot_id": source_snapshot_id,
            }
            digest = _content_hash(tmp, **params)  # type: ignore[arg-type]
            os.replace(tmp / _BARS_DIR, directory / _BARS_DIR)
            os.replace(tmp / _ACTIONS_NAME, directory / _ACTIONS_NAME)
            if _content_hash(directory, **params) != digest:  # type: ignore[arg-type]
                raise HoldoutStoreError("content hash changed while moving files into place")
            manifest = HoldoutManifest(
                source_snapshot_id=source_snapshot_id,
                research_end=windows.research_end,
                holdout_start=windows.holdout_start,
                holdout_end=windows.holdout_end,
                holdout_years=holdout_years,
                embargo_sessions=windows.embargo_sessions,
                content_sha256=digest,
                carved_at=carved_at,
                code_version=code_version,
            )
            _write_manifest(directory, manifest)
            return manifest
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


def load_manifest(directory: Path) -> HoldoutManifest:
    path = directory / MANIFEST_NAME
    try:
        return HoldoutManifest.model_validate_json(path.read_text())
    except FileNotFoundError as e:
        raise HoldoutManifestError(f"holdout manifest missing: {path}") from e
    except (OSError, ValidationError, ValueError) as e:
        raise HoldoutManifestError(f"holdout manifest unreadable: {path}: {e}") from e


def verify_holdout_dir(directory: Path) -> HoldoutManifest:
    try:
        mode = stat.S_IMODE(directory.stat().st_mode)
    except OSError as e:
        raise HoldoutManifestError(f"holdout directory unreadable: {directory}: {e}") from e
    if mode & _GROUP_OTHER_BITS:
        raise HoldoutPermissionError(
            f"holdout directory {directory} is accessible to group/others "
            f"(mode {mode:o}); fix with: chmod 700 {directory}"
        )
    manifest = load_manifest(directory)
    if _hash_for(directory, manifest) != manifest.content_sha256:
        raise HoldoutManifestError(f"holdout content does not match manifest hash: {directory}")
    return manifest


def read_holdout(directory: Path) -> tuple[list[Bar], list[CorporateAction]]:
    verify_holdout_dir(directory)
    cache = ParquetCache(directory)
    bars: list[Bar] = []
    for path in sorted((directory / _BARS_DIR).glob("*.parquet")):
        bars.extend(cache.load(path.stem))
    bars.sort(key=lambda b: (b.ts, b.symbol))
    return bars, ActionsStore(directory).load()


def holdout_provider(
    research_bars: Sequence[Bar],
    research_actions: Sequence[CorporateAction],
    directory: Path,
) -> ComposedDataProvider:
    """Research + embargo + holdout on one basis; adjustment happens per read at `end`."""
    held_bars, held_actions = read_holdout(directory)
    return ComposedDataProvider(
        [*research_bars, *held_bars],
        corporate_actions=[*research_actions, *held_actions],
    )
