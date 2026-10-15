"""Shared contract suite.

Every implementation of a contract (fake today, real in later phases) must pass these tests.
To cover a new implementation, add its factory to the `params` list of the matching fixture.
"""

from collections.abc import Callable, Sequence

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

ProviderFactory = Callable[[Sequence[Bar]], DataProvider]


@pytest.fixture(params=[InMemoryRegistry], ids=["in_memory"])
def registry(request: pytest.FixtureRequest) -> Registry:
    return request.param()  # type: ignore[no-any-return]


@pytest.fixture(params=[InMemoryRunLog], ids=["in_memory"])
def runlog(request: pytest.FixtureRequest) -> RunLog:
    return request.param()  # type: ignore[no-any-return]


@pytest.fixture(params=[InMemoryCommandInbox], ids=["in_memory"])
def inbox(request: pytest.FixtureRequest) -> CommandInbox:
    return request.param()  # type: ignore[no-any-return]


@pytest.fixture(params=[InMemoryDataProvider], ids=["in_memory"])
def make_provider(request: pytest.FixtureRequest) -> ProviderFactory:
    factory = request.param
    return lambda bars: factory(bars)


@pytest.fixture(params=[ImpactCostModel], ids=["impact_fake"])
def cost_model(request: pytest.FixtureRequest) -> CostModel:
    return request.param()  # type: ignore[no-any-return]


@pytest.fixture(params=[ExpandingWindowSplitter], ids=["expanding_fake"])
def splitter(request: pytest.FixtureRequest) -> Splitter:
    return request.param()  # type: ignore[no-any-return]
