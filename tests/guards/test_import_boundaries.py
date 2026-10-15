import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
LINT_IMPORTS = Path(sys.executable).parent / "lint-imports"
IGNORE = shutil.ignore_patterns(
    ".venv",
    ".git",
    ".worktrees",
    "__pycache__",
    ".mypy_cache",
    ".ruff_cache",
    ".pytest_cache",
    ".hypothesis",
)


def lint(root: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(LINT_IMPORTS)], cwd=root, capture_output=True, text=True, check=False
    )


@pytest.fixture
def repo_copy(tmp_path: Path) -> Path:
    target = tmp_path / "repo"
    shutil.copytree(REPO, target, ignore=IGNORE)
    return target


def test_clean_repo_passes(repo_copy: Path) -> None:
    result = lint(repo_copy)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize(
    ("path", "code", "contract"),
    [
        ("strategies/evil.py", "import broker\n", "strategies may import only contracts"),
        ("strategies/evil.py", "import harness\n", "strategies may import only contracts"),
        ("strategies/evil.py", "import engine\n", "strategies may import only contracts"),
        ("harness/evil.py", "import strategies\n", "harness must not depend"),
        ("harness/evil.py", "import broker\n", "broker is reachable only from risk and engine"),
        ("proposer/evil.py", "import broker\n", "broker is reachable only from risk and engine"),
        ("contracts/evil.py", "import fakes\n", "contracts is a leaf package"),
    ],
)
def test_planted_violation_fails_ci(repo_copy: Path, path: str, code: str, contract: str) -> None:
    (repo_copy / path).write_text(code)
    result = lint(repo_copy)
    assert result.returncode != 0
    assert contract in result.stdout
    assert "BROKEN" in result.stdout


@pytest.mark.parametrize(
    ("path", "code"),
    [
        ("strategies/evil.py", "import proposer\n"),
        ("strategies/evil.py", "import fakes\n"),
        ("strategies/evil.py", "import risk\n"),
        ("engine/evil.py", "import proposer\n"),
        ("risk/evil.py", "import proposer\n"),
        ("broker/evil.py", "import proposer\n"),
        ("harness/evil.py", "import proposer\n"),
        ("contracts/evil.py", "import proposer\n"),
    ],
)
def test_nothing_may_import_the_proposer_and_strategies_stay_isolated(
    repo_copy: Path, path: str, code: str
) -> None:
    (repo_copy / path).write_text(code)
    result = lint(repo_copy)
    assert result.returncode != 0
    assert "BROKEN" in result.stdout
