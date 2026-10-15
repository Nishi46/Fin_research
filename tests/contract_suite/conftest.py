"""Shared contract suite.

Every implementation of a contract (fake today, real in later phases) must pass these tests.
Real implementations are offered by the harness areas through `harness.testing.factories`, so
they are picked up here without editing this file.
"""

from collections.abc import Callable, Mapping, Sequence

import pytest

from contracts.costs import CostModel
from contracts.data import Bar, DataProvider
from contracts.registry import Registry
from contracts.runtime import CommandInbox, RunLog
from contracts.splitting import Splitter
from fakes.costs import ImpactCostModel
from fakes.data import InMemoryDataProvider
from fakes.registry import InMemoryRegistry
from fakes.runtime import InMemoryCommandInbox, InMemoryRunLog
from fakes.splitting import ExpandingWindowSplitter
from harness.testing import factories as real

ProviderFactory = Callable[[Sequence[Bar]], DataProvider]


def _with_real(
    fake: Callable[..., object], fake_id: str, table: Mapping[str, Callable[..., object]]
) -> list[object]:
    """The in-memory fake, plus every real implementation offered by the harness."""
    return [pytest.param(fake, id=fake_id), *(pytest.param(f, id=n) for n, f in table.items())]


@pytest.fixture(params=_with_real(InMemoryRegistry, "in_memory", real.REGISTRY_FACTORIES))
def registry(request: pytest.FixtureRequest) -> Registry:
    return request.param()  # type: ignore[no-any-return]


@pytest.fixture(params=_with_real(InMemoryRunLog, "in_memory", real.RUNLOG_FACTORIES))
def runlog(request: pytest.FixtureRequest) -> RunLog:
    return request.param()  # type: ignore[no-any-return]


@pytest.fixture(params=_with_real(InMemoryCommandInbox, "in_memory", real.INBOX_FACTORIES))
def inbox(request: pytest.FixtureRequest) -> CommandInbox:
    return request.param()  # type: ignore[no-any-return]


@pytest.fixture(params=_with_real(InMemoryDataProvider, "in_memory", real.PROVIDER_FACTORIES))
def make_provider(request: pytest.FixtureRequest) -> ProviderFactory:
    factory = request.param
    return lambda bars: factory(bars)


@pytest.fixture(params=_with_real(ImpactCostModel, "impact_fake", real.COST_MODEL_FACTORIES))
def cost_model(request: pytest.FixtureRequest) -> CostModel:
    return request.param()  # type: ignore[no-any-return]


@pytest.fixture(
    params=_with_real(ExpandingWindowSplitter, "expanding_fake", real.SPLITTER_FACTORIES)
)
def splitter(request: pytest.FixtureRequest) -> Splitter:
    return request.param()  # type: ignore[no-any-return]
