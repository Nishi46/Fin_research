"""Offers the real cost model to the shared contract suite."""

from collections.abc import Callable

from contracts.costs import CostModel
from harness.costs.model import RealCostModel

COST_MODEL_FACTORIES: dict[str, Callable[[], CostModel]] = {
    "real": RealCostModel,
}
