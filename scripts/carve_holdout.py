"""Carve the hidden final holdout out of freshly fetched data. OWNER-RUN, IRREVERSIBLE.

    uv run python -m scripts.carve_holdout --symbols SPY,QQQ,... [--years 10]
        [--dry-run] [--allow-stale] [--prune-cache]

Environment: FIN_RESEARCH_HOLDOUT_DIR (existing directory outside the repo), LAB_DATA_CACHE_DIR
(research cache), ALPACA_API_KEY / ALPACA_API_SECRET. The symbol list has no default: the
universe is the owner's decision. Run `--dry-run` first; it fetches and validates but writes
nothing.

Reminders:
- Carving is irreversible and re-carving is refused. Do not run this for real until the owner has
  approved the symbol list.
- Research providers must be built from already-capped data AND membership data: `ResearchWindow`
  can only clamp the date for membership, it cannot remove later membership changes.
- Only counts, dates and the snapshot id are printed; never bar-level holdout data.

Exit codes: 0 success or dry run, 1 refusal (already carved, snapshot rejected, stale data,
floors, stale cache), 2 usage or configuration error.
"""

import argparse
import os
import subprocess
import sys
from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from contracts.data import Bar, CorporateAction
from harness.data.alpaca import AlpacaDataClient, UrllibHttpClient
from harness.data.cache import ActionsStore, ParquetCache
from harness.data.calendar import trading_sessions
from harness.data.config import DataConfig
from harness.data.crosscheck import YFinanceCrossChecker
from harness.data.holdout_store import (
    MANIFEST_NAME,
    HoldoutAlreadyCarvedError,
    HoldoutStoreError,
    write_holdout,
)
from harness.data.provider import ComposedDataProvider, CrossChecker
from harness.data.ratelimit import RateLimiter
from harness.data.reconcile import SnapshotRejected
from harness.data.split import SplitData, split_data
from harness.data.window import Windows, compute_windows
from harness.thresholds import DEFAULT_THRESHOLDS, Thresholds
from harness.validation.holdout import (
    HOLDOUT_ENV_VAR,
    HoldoutLocationError,
    HoldoutNotConfiguredError,
    resolve_holdout_dir,
)

DEFAULT_YEARS = 10
STALE_AFTER_SESSIONS = 5
YAHOO_CALLS_PER_MINUTE = 20
_NY = ZoneInfo("America/New_York")
_UTC_MIDNIGHT_ACTIONS = UTC

FetchBars = Callable[[Sequence[str], datetime, datetime], list[Bar]]
FetchActions = Callable[[Sequence[str], datetime, datetime], list[CorporateAction]]


def _bar_session(bar: Bar) -> date:
    return bar.ts.astimezone(_NY).date()


def _action_session(action: CorporateAction) -> date:
    return action.effective_at.astimezone(_UTC_MIDNIGHT_ACTIONS).date()


def _stale_sessions(last: date, now: datetime) -> int:
    today = now.astimezone(_NY).date()
    if today <= last:
        return 0
    return len(trading_sessions(last + timedelta(days=1), today))


def _describe(split: SplitData, windows: Windows, out: Callable[[str], None]) -> None:
    out(f"research: through {windows.research_end}  bars={len(split.research_bars)}")
    out(f"embargo:  {windows.embargo_sessions} sessions  bars={len(split.embargo_bars)}")
    out(
        f"holdout:  {windows.holdout_start} .. {windows.holdout_end}  "
        f"bars={len(split.holdout_bars)}"
    )
    spans: dict[str, list[date]] = defaultdict(list)
    for b in [*split.research_bars, *split.embargo_bars, *split.holdout_bars]:
        spans[b.symbol].append(_bar_session(b))
    for symbol in sorted(spans):
        out(f"  {symbol}: {min(spans[symbol])} .. {max(spans[symbol])}")


def _stale_cache(cache_dir: Path, research_end: date) -> list[Path]:
    stale: list[Path] = []
    bars_dir = cache_dir / "bars"
    cache = ParquetCache(cache_dir)
    if bars_dir.is_dir():
        for path in sorted(bars_dir.glob("*.parquet")):
            if any(_bar_session(b) > research_end for b in cache.load(path.stem)):
                stale.append(path)
    actions_path = cache_dir / "actions.parquet"
    if actions_path.is_file() and any(
        _action_session(a) > research_end for a in ActionsStore(cache_dir).load()
    ):
        stale.append(actions_path)
    return stale


def _prune(cache_dir: Path, stale: Sequence[Path], research_end: date) -> None:
    """Rewrite only the listed cache files, dropping rows after research_end."""
    for path in stale:
        if path.name == "actions.parquet":
            store = ActionsStore(cache_dir)
            store.store([a for a in store.load() if _action_session(a) <= research_end])
            continue
        cache = ParquetCache(cache_dir)
        kept = [b for b in cache.load(path.stem) if _bar_session(b) <= research_end]
        if kept:
            cache.store(path.stem, kept)
        else:
            path.unlink()


def _write_research(cache_dir: Path, split: SplitData, research_end: date) -> None:
    by_symbol: dict[str, list[Bar]] = defaultdict(list)
    for b in split.research_bars:
        by_symbol[b.symbol].append(b)
    actions = split.research_actions
    # Last line of defence: the research cache must never see data past research_end.
    assert all(_bar_session(b) <= research_end for bs in by_symbol.values() for b in bs)
    assert all(_action_session(a) <= research_end for a in actions)
    cache = ParquetCache(cache_dir)
    for symbol, bars in by_symbol.items():
        cache.store(symbol, bars)
    ActionsStore(cache_dir).store(actions)


def carve(
    *,
    symbols: Sequence[str],
    years: int,
    holdout_dir: Path,
    cache_dir: Path,
    fetch_bars: FetchBars,
    fetch_actions: FetchActions,
    cross_checkers: Sequence[CrossChecker],
    now: datetime,
    thresholds: Thresholds = DEFAULT_THRESHOLDS,
    dry_run: bool = False,
    allow_stale: bool = False,
    prune_cache: bool = False,
    code_version: str = "unknown",
    out: Callable[[str], None] = print,
) -> int:
    manifest_path = holdout_dir / MANIFEST_NAME
    if manifest_path.exists():
        out(f"refused: holdout already carved ({manifest_path}). Re-carving is not allowed.")
        return 1

    start = now - timedelta(days=365 * years)
    bars = fetch_bars(symbols, start, now)
    actions = fetch_actions(symbols, start, now)
    if not bars:
        out("refused: no bars fetched")
        return 1

    provider = ComposedDataProvider(
        bars, corporate_actions=actions, cross_checkers=cross_checkers, strict=True
    )
    try:
        snapshot_id = provider.snapshot().id
    except SnapshotRejected as err:
        out(f"refused: snapshot rejected, nothing written: {err}")
        return 1
    out(f"snapshot: {snapshot_id}")

    sessions = sorted({_bar_session(b) for b in bars})
    behind = _stale_sessions(sessions[-1], now)
    if behind > STALE_AFTER_SESSIONS and not allow_stale:
        out(
            f"refused: stale data, last session {sessions[-1]} is {behind} sessions old "
            f"(limit {STALE_AFTER_SESSIONS}); pass --allow-stale to override"
        )
        return 1

    try:
        windows = compute_windows(
            sessions, thresholds.holdout_years, thresholds.holdout_embargo_sessions
        )
        split = split_data(bars, actions, windows, thresholds=thresholds)
    except ValueError as err:
        out(f"refused: {err}")
        return 1
    _describe(split, windows, out)

    if dry_run:
        out("dry run: nothing written")
        return 0

    stale = _stale_cache(cache_dir, windows.research_end) if cache_dir.is_dir() else []
    if stale and not prune_cache:
        out(
            f"refused: research cache holds data after research_end {windows.research_end} "
            f"in {len(stale)} file(s); re-run with --prune-cache to trim them"
        )
        return 1

    try:
        manifest = write_holdout(
            holdout_dir,
            split,
            windows,
            source_snapshot_id=snapshot_id,
            holdout_years=thresholds.holdout_years,
            code_version=code_version,
            now=now,
        )
    except HoldoutAlreadyCarvedError:
        out("refused: holdout already carved. Re-carving is not allowed.")
        return 1
    except HoldoutStoreError as err:
        out(f"refused: {err}")
        return 1

    if stale:
        _prune(cache_dir, stale, windows.research_end)
    _write_research(cache_dir, split, windows.research_end)
    out(f"manifest: {manifest_path} (content {manifest.content_sha256[:12]})")
    out("The holdout is now frozen. Re-carving is refused; do not copy it into the repository.")
    return 0


def _code_version() -> str:
    try:
        done = subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True, timeout=10
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    return done.stdout.strip() or "unknown"


def _run(env: Mapping[str, str], symbols: list[str], args: argparse.Namespace) -> int:
    holdout_dir = resolve_holdout_dir(env)
    config = DataConfig.from_env(env)
    from harness.data.yfinance_fetcher import make_yfinance_fetcher

    alpaca = AlpacaDataClient(
        UrllibHttpClient(), config, RateLimiter(max_calls=config.calls_per_minute, period_s=60.0)
    )
    checkers = [
        YFinanceCrossChecker(
            make_yfinance_fetcher(RateLimiter(max_calls=YAHOO_CALLS_PER_MINUTE, period_s=60.0))
        )
    ]
    return carve(
        symbols=symbols,
        years=args.years,
        holdout_dir=holdout_dir,
        cache_dir=config.cache_dir,
        fetch_bars=alpaca.fetch_bars,
        fetch_actions=alpaca.fetch_corporate_actions,
        cross_checkers=checkers,
        now=datetime.now(UTC),
        dry_run=args.dry_run,
        allow_stale=args.allow_stale,
        prune_cache=args.prune_cache,
        code_version=_code_version(),
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--symbols", required=True, help="comma-separated; no default universe")
    parser.add_argument("--years", type=int, default=DEFAULT_YEARS)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--allow-stale", action="store_true")
    parser.add_argument("--prune-cache", action="store_true")
    args = parser.parse_args(argv)
    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    if not symbols:
        print("cannot run: --symbols is empty")
        return 2
    try:
        return _run(os.environ, symbols, args)
    except (
        ValueError,
        ModuleNotFoundError,
        HoldoutNotConfiguredError,
        HoldoutLocationError,
    ) as err:
        print(f"cannot run ({HOLDOUT_ENV_VAR}, LAB_DATA_CACHE_DIR, Alpaca keys): {err}")
        return 2


if __name__ == "__main__":
    sys.exit(main())
