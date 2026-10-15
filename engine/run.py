import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from contracts.capital import CapitalConfig
from contracts.data import DataProvider
from contracts.dataview import DataView
from contracts.intent import Intent
from contracts.runtime import RunEvent, RunLog, RunStatus
from contracts.strategy import Strategy
from engine.config import EngineConfig


class AsofMismatchError(ValueError):
    """A strategy returned an intent stamped with a different time than it was asked for."""


@dataclass(frozen=True)
class RunResult:
    run_id: uuid.UUID
    intent: Intent
    capital: CapitalConfig


def _utcnow() -> datetime:
    return datetime.now(UTC)


def run_once(
    *,
    config: EngineConfig,
    provider: DataProvider,
    strategy: Strategy,
    runlog: RunLog,
    asof: datetime,
    clock: Callable[[], datetime] = _utcnow,
) -> RunResult:
    """One engine cycle up to the strategy's intent. No sizing, no broker, no orders yet:
    those arrive with the risk layer (Phase 2) and the Alpaca adapter (Phase 3).
    """
    run_id = uuid.uuid4()

    def log(status: RunStatus, detail: str | None = None) -> None:
        runlog.append(
            RunEvent(
                event_id=uuid.uuid4(),
                run_id=run_id,
                at=clock(),
                status=status,
                host=config.host,
                detail=detail,
            )
        )

    log(RunStatus.STARTED)
    try:
        view = DataView(provider, asof)
        intent = strategy.generate(view, view.asof)
        if intent.asof != view.asof:
            raise AsofMismatchError(f"intent asof {intent.asof} does not match the run's asof")
    except Exception as exc:
        log(RunStatus.FAILED, f"{type(exc).__name__}: {exc}")
        raise
    log(RunStatus.SUCCEEDED)
    return RunResult(run_id=run_id, intent=intent, capital=config.capital)
