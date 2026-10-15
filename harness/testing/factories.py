"""Real implementations offered to the shared contract suite.

The suite in `tests/contract_suite/` is locked, so teammates cannot add their implementations to
it. Instead each area exposes its own `factories.py` with tables named as below, and this module
merges them. The suite's fixtures read the tables from here.

Area modules and the tables they may define (all `dict[str, Callable]`, id -> factory):

    harness.registry.factories    REGISTRY_FACTORIES, RUNLOG_FACTORIES, INBOX_FACTORIES
    harness.data.factories        PROVIDER_FACTORIES     (bars -> DataProvider)
    harness.costs.factories       COST_MODEL_FACTORIES   (() -> CostModel)
    harness.validation.factories  SPLITTER_FACTORIES     (() -> Splitter)

An area that does not exist yet contributes nothing. An area that exists but fails to import
raises, so a broken implementation cannot silently drop out of the suite.
"""

import importlib
from collections.abc import Callable, Iterable, Mapping
from types import MappingProxyType
from typing import Any

Factory = Callable[..., Any]

_AREA_FOR_TABLE: Mapping[str, str] = {
    "REGISTRY_FACTORIES": "harness.registry.factories",
    "RUNLOG_FACTORIES": "harness.registry.factories",
    "INBOX_FACTORIES": "harness.registry.factories",
    "PROVIDER_FACTORIES": "harness.data.factories",
    "COST_MODEL_FACTORIES": "harness.costs.factories",
    "SPLITTER_FACTORIES": "harness.validation.factories",
}


def collect(table: str, modules: Iterable[str]) -> Mapping[str, Factory]:
    merged: dict[str, Factory] = {}
    for module_name in modules:
        try:
            module = importlib.import_module(module_name)
        except ModuleNotFoundError as err:
            # Only "the area is not built yet" is acceptable; a missing dependency is a bug.
            missing = err.name or ""
            if module_name == missing or module_name.startswith(f"{missing}."):
                continue
            raise
        for factory_id, factory in getattr(module, table, {}).items():
            if factory_id in merged:
                raise ValueError(f"duplicate factory id {factory_id!r} in {table}")
            merged[factory_id] = factory
    return MappingProxyType(merged)


def _table(name: str) -> Mapping[str, Factory]:
    return collect(name, (_AREA_FOR_TABLE[name],))


REGISTRY_FACTORIES = _table("REGISTRY_FACTORIES")
RUNLOG_FACTORIES = _table("RUNLOG_FACTORIES")
INBOX_FACTORIES = _table("INBOX_FACTORIES")
PROVIDER_FACTORIES = _table("PROVIDER_FACTORIES")
COST_MODEL_FACTORIES = _table("COST_MODEL_FACTORIES")
SPLITTER_FACTORIES = _table("SPLITTER_FACTORIES")

ALL_TABLES: Mapping[str, Mapping[str, Factory]] = MappingProxyType(
    {
        "registry": REGISTRY_FACTORIES,
        "runlog": RUNLOG_FACTORIES,
        "inbox": INBOX_FACTORIES,
        "provider": PROVIDER_FACTORIES,
        "cost_model": COST_MODEL_FACTORIES,
        "splitter": SPLITTER_FACTORIES,
    }
)
