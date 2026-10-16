"""Factory tables the shared contract suite collects (see harness.testing.factories)."""

from collections.abc import Callable

from contracts.splitting import Splitter
from harness.validation.splitter import PurgedWalkForwardSplitter

SPLITTER_FACTORIES: dict[str, Callable[[], Splitter]] = {
    "purged_walk_forward": PurgedWalkForwardSplitter
}
