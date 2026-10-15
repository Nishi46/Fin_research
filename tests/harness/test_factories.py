from collections.abc import Callable, Mapping

import pytest

from harness.testing import factories

KINDS = (
    "REGISTRY_FACTORIES",
    "RUNLOG_FACTORIES",
    "INBOX_FACTORIES",
    "PROVIDER_FACTORIES",
    "COST_MODEL_FACTORIES",
    "SPLITTER_FACTORIES",
)


@pytest.mark.parametrize("kind", KINDS)
def test_every_contract_has_a_factory_table(kind: str) -> None:
    table = getattr(factories, kind)
    assert isinstance(table, Mapping)


@pytest.mark.parametrize("kind", KINDS)
def test_entries_are_named_and_callable(kind: str) -> None:
    table: Mapping[str, Callable[..., object]] = getattr(factories, kind)
    for name, factory in table.items():
        assert name.strip(), "factory needs a non-empty id for test output"
        assert callable(factory)


@pytest.mark.parametrize("kind", KINDS)
def test_tables_are_read_only_views(kind: str) -> None:
    table = getattr(factories, kind)
    with pytest.raises(TypeError):
        table["sneaky"] = lambda: None


def test_missing_area_module_contributes_nothing() -> None:
    assert dict(factories.collect("T", ("harness.no_such_area.factories",))) == {}


def test_area_without_the_table_contributes_nothing() -> None:
    assert dict(factories.collect("NOT_A_TABLE", ("harness.thresholds",))) == {}


def test_existing_area_module_contributes_its_table(monkeypatch: pytest.MonkeyPatch) -> None:
    import sys
    import types

    fake = types.ModuleType("harness_fake_area")
    fake.__dict__["T"] = {"ok": lambda: None}
    monkeypatch.setitem(sys.modules, "harness_fake_area", fake)
    assert set(factories.collect("T", ("harness_fake_area",))) == {"ok"}


def test_import_error_inside_an_existing_area_propagates(monkeypatch: pytest.MonkeyPatch) -> None:
    import importlib

    def boom(name: str) -> object:
        raise ModuleNotFoundError("No module named 'numpyy'", name="numpyy")

    monkeypatch.setattr(importlib, "import_module", boom)
    with pytest.raises(ModuleNotFoundError):
        factories.collect("T", ("harness.data.factories",))


def test_duplicate_factory_ids_across_areas_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    import sys
    import types

    for mod in ("harness_fake_a", "harness_fake_b"):
        m = types.ModuleType(mod)
        m.__dict__["T"] = {"same": lambda: None}
        monkeypatch.setitem(sys.modules, mod, m)
    with pytest.raises(ValueError, match="same"):
        factories.collect("T", ("harness_fake_a", "harness_fake_b"))


def test_all_tables_lists_exactly_the_kinds() -> None:
    assert set(factories.ALL_TABLES) == {k.removesuffix("_FACTORIES").lower() for k in KINDS}
