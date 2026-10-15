"""Tripwire for agent-written strategy code.

Strategies may do arithmetic on the data they are given and nothing else. This AST check
rejects imports outside a small allow-list and the usual ways of escaping or poking at the
DataView guard. It is a tripwire, not a sandbox: determined code can get around a static
check. It catches honest mistakes and the obvious escapes (getattr, vars, `os.system` through
an allowed module). Numeric libraries are
left off the allow-list for now because they can read files; add them deliberately when the
process-level sandbox (Phase 4) exists. Real isolation is that sandbox, not this check.
"""

import ast
import sys
from pathlib import Path

ALLOWED_IMPORTS = {
    "__future__",
    "abc",
    "collections",
    "contracts",
    "dataclasses",
    "datetime",
    "decimal",
    "enum",
    "functools",
    "itertools",
    "math",
    "statistics",
    "strategies",
    "typing",
}
FORBIDDEN_CALLS = {
    "__import__",
    "open",
    "eval",
    "exec",
    "compile",
    "breakpoint",
    "getattr",
    "setattr",
    "delattr",
    "vars",
    "dir",
    "globals",
    "locals",
    "type",
}
# Names that give away an attempt to reach the DataView guard or an escape route.
FORBIDDEN_STRINGS = ("_provider", "_asof")
FORBIDDEN_ATTRIBUTES = {
    "_provider",
    "_asof",
    "__dict__",
    "__class__",
    "__globals__",
    "__builtins__",
    "__subclasses__",
    "__code__",
    "__import__",
    "__getattribute__",
    "__reduce__",
    "__reduce_ex__",
    "__getstate__",
    "__setattr__",
    "os",
    "sys",
    "modules",
    "system",
    "popen",
    "subprocess",
    "builtins",
}


def _check(tree: ast.AST) -> list[tuple[int, str]]:
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] not in ALLOWED_IMPORTS:
                    found.append((node.lineno, f"import of {alias.name} is not allowed"))
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and (node.module or "").split(".")[0] not in ALLOWED_IMPORTS:
                found.append((node.lineno, f"import from {node.module} is not allowed"))
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            # Any reference, not just a call: `f = getattr` would otherwise slip through.
            if node.id in FORBIDDEN_CALLS:
                found.append((node.lineno, f"use of {node.id} is not allowed"))
        elif isinstance(node, ast.Attribute) and node.attr in FORBIDDEN_ATTRIBUTES:
            found.append((node.lineno, f"access to .{node.attr} is not allowed"))
        elif (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and any(name in node.value for name in FORBIDDEN_STRINGS)
        ):
            found.append((node.lineno, "string mentions guard internals"))
    return found


def find_violations(root: Path) -> list[str]:
    violations: list[str] = []
    for path in sorted((root / "strategies").rglob("*.py")):
        tree = ast.parse(path.read_text(), filename=str(path))
        rel = path.relative_to(root).as_posix()
        violations.extend(f"{rel}:{line}: {message}" for line, message in _check(tree))
    return violations


def main() -> int:
    root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path.cwd()
    violations = find_violations(root)
    for violation in violations:
        print(violation)
    return 1 if violations else 0


if __name__ == "__main__":
    sys.exit(main())
