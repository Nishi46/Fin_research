from pathlib import Path

import pytest

from tools.check_capital_literals import find_violations

REPO = Path(__file__).resolve().parents[2]


def write(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


@pytest.mark.parametrize(
    "line",
    [
        "capital = 10000",
        "capital = 10_000",
        "capital = 10000.0",
        'capital = Decimal("10000")',
        'capital = Decimal("10_000")',
    ],
)
def test_planted_literal_is_flagged(tmp_path: Path, line: str) -> None:
    write(tmp_path, "strategies/bad.py", f"{line}\n")
    violations = find_violations(tmp_path)
    assert len(violations) == 1
    assert "strategies/bad.py:1" in violations[0]


def test_other_numbers_are_fine(tmp_path: Path) -> None:
    write(tmp_path, "risk/ok.py", "pdt_threshold = 25000\nn = 10001\ns = 'hello'\n")
    assert find_violations(tmp_path) == []


def test_default_definition_and_tests_are_exempt(tmp_path: Path) -> None:
    write(tmp_path, "contracts/capital.py", 'DEFAULT = Decimal("10000")\n')
    write(tmp_path, "tests/unit/test_x.py", "x = 10000\n")
    assert find_violations(tmp_path) == []


def test_explicit_marker_allows_a_non_capital_use(tmp_path: Path) -> None:
    write(tmp_path, "fakes/ok.py", "BPS = 10_000.0  # capital-literal-ok: basis points\n")
    assert find_violations(tmp_path) == []


def test_virtualenv_and_worktrees_are_skipped(tmp_path: Path) -> None:
    write(tmp_path, ".venv/lib/x.py", "x = 10000\n")
    write(tmp_path, ".worktrees/other/y.py", "x = 10000\n")
    assert find_violations(tmp_path) == []


def test_the_real_repo_is_clean() -> None:
    assert find_violations(REPO) == []
