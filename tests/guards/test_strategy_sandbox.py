from pathlib import Path

import pytest

from tools.check_strategy_sandbox import find_violations

REPO = Path(__file__).resolve().parents[2]


def plant(root: Path, code: str) -> list[str]:
    (root / "strategies").mkdir(parents=True, exist_ok=True)
    (root / "strategies" / "x.py").write_text(code)
    return find_violations(root)


@pytest.mark.parametrize(
    "code",
    [
        "import os\n",
        "import subprocess\n",
        "import socket\n",
        "import sqlite3\n",
        "import importlib\n",
        "import ctypes\n",
        "import builtins\n",
        "from pathlib import Path\n",
        "from os import path\n",
        "import broker\n",
    ],
)
def test_risky_imports_are_flagged(tmp_path: Path, code: str) -> None:
    assert len(plant(tmp_path, code)) == 1


@pytest.mark.parametrize(
    "code",
    [
        "x = __import__('os')\n",
        "x = open('f')\n",
        "eval('1')\n",
        "exec('x=1')\n",
        "compile('1', 'f', 'eval')\n",
    ],
)
def test_dynamic_escape_calls_are_flagged(tmp_path: Path, code: str) -> None:
    assert len(plant(tmp_path, code)) == 1


@pytest.mark.parametrize(
    "code",
    [
        "p = view._provider\n",
        "view._asof = 5\n",
        "x = view.__class__.__dict__\n",
        "x = f.__globals__\n",
        "x = ().__class__.__subclasses__()\n",
    ],
)
def test_poking_at_guard_internals_is_flagged(tmp_path: Path, code: str) -> None:
    assert plant(tmp_path, code)


@pytest.mark.parametrize(
    "code",
    [
        "x = getattr(d, '_pro' + 'vider')\n",
        "f = getattr\n",
        "setattr(d, 'x', 1)\n",
        "x = vars(d)\n",
        "x = dir(d)\n",
        "x = globals()\n",
        "x = locals()\n",
        "x = type(d)\n",
        "x = d.__getattribute__('x')\n",
        "x = d.__reduce__()\n",
        "x = collections.os.system('id')\n",
        "x = functools.sys.modules['os']\n",
        "x = '_provider'\n",
        "import numpy\n",
        "import pandas\n",
        "from scipy import stats\n",
    ],
)
def test_common_escape_hatches_are_flagged(tmp_path: Path, code: str) -> None:
    assert plant(tmp_path, code)


def test_ordinary_strategy_code_is_fine(tmp_path: Path) -> None:
    code = (
        "from __future__ import annotations\n"
        "import math\n"
        "from collections.abc import Sequence\n"
        "from datetime import datetime\n"
        "from contracts.intent import Intent\n"
        "from . import helper\n"
    )
    assert plant(tmp_path, code) == []


def test_the_real_strategies_directory_is_clean() -> None:
    assert find_violations(REPO) == []
