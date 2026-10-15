"""Fail CI when the default account size appears as a literal outside its one definition.

Capital must flow through `CapitalConfig`. A genuine non-capital use of the same number
(for example basis points) can opt out with a `# capital-literal-ok: <reason>` comment.
"""

import ast
import sys
from pathlib import Path

SKIP_DIRS = {
    ".venv",
    ".worktrees",
    ".git",
    "__pycache__",
    "node_modules",
    ".mypy_cache",
    ".ruff_cache",
    ".pytest_cache",
    ".hypothesis",
}
EXEMPT_FILES = {"contracts/capital.py", "tools/check_capital_literals.py"}
EXEMPT_TOP_DIRS = {"tests"}
MARKER = "capital-literal-ok"
FORBIDDEN_NUMBER = 10_000
FORBIDDEN_STRINGS = {"10000", "10_000", "10,000", "10000.0", "10000.00"}


def _is_forbidden(value: object) -> bool:
    if isinstance(value, bool):
        return False
    if isinstance(value, int | float):
        return value == FORBIDDEN_NUMBER
    if isinstance(value, str):
        return value.strip() in FORBIDDEN_STRINGS
    return False


def _python_files(root: Path) -> list[Path]:
    files = []
    for path in sorted(root.rglob("*.py")):
        rel = path.relative_to(root)
        if SKIP_DIRS.intersection(rel.parts):
            continue
        if rel.parts[0] in EXEMPT_TOP_DIRS or rel.as_posix() in EXEMPT_FILES:
            continue
        files.append(path)
    return files


def find_violations(root: Path) -> list[str]:
    violations = []
    for path in _python_files(root):
        source = path.read_text()
        lines = source.splitlines()
        for node in ast.walk(ast.parse(source, filename=str(path))):
            if isinstance(node, ast.Constant) and _is_forbidden(node.value):
                line = lines[node.lineno - 1]
                if MARKER in line:
                    continue
                rel = path.relative_to(root).as_posix()
                violations.append(
                    f"{rel}:{node.lineno}: hard-coded account size; use CapitalConfig "
                    f"(or add '# {MARKER}: <reason>')"
                )
    return violations


def main() -> int:
    root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path.cwd()
    violations = find_violations(root)
    for violation in violations:
        print(violation)
    return 1 if violations else 0


if __name__ == "__main__":
    sys.exit(main())
