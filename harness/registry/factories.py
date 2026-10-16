"""Factory tables the shared contract suite collects (see harness.testing.factories)."""

from collections.abc import Callable

from contracts.registry import Registry
from contracts.runtime import CommandInbox, RunLog
from harness.registry.sqlite import SqliteRegistry
from harness.registry.sqlite_runtime import SqliteCommandInbox, SqliteRunLog

REGISTRY_FACTORIES: dict[str, Callable[[], Registry]] = {"sqlite": SqliteRegistry}
RUNLOG_FACTORIES: dict[str, Callable[[], RunLog]] = {"sqlite": SqliteRunLog}
INBOX_FACTORIES: dict[str, Callable[[], CommandInbox]] = {"sqlite": SqliteCommandInbox}
