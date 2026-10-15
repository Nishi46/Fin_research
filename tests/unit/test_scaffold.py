import importlib

import pytest

PACKAGES = [
    "contracts",
    "fakes",
    "engine",
    "strategies",
    "harness",
    "risk",
    "broker",
    "proposer",
    "tools",
]


@pytest.mark.parametrize("name", PACKAGES)
def test_package_importable(name: str) -> None:
    assert importlib.import_module(name) is not None
