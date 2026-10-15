#!/usr/bin/env python3
"""PreToolUse hook: block agent writes to protected paths.

Protected paths are listed in `.claude/protected_paths.txt` (one per line; a trailing slash
means a directory). The owner sets `LAB_UNLOCK_PROTECTED=1` when launching Claude Code to
work on them deliberately.

What this is: a guard on Claude Code's own file tools, plus a best-effort tripwire on Bash
commands. What it is not: a sandbox. A command that avoids the patterns below, a formatter
that rewrites files, a glob that expands to a protected path, or a user at their own terminal
all get through. See docs/PROTECTIONS.md for the full list of gaps.

Fails closed: malformed input, a missing protected-paths file, or any unexpected error blocks
the tool call (exit 2). It cannot protect against the hook itself failing to start (for
example python3 missing, exit 127), which Claude Code treats as non-blocking.

Kept compatible with the system python3 (3.9), since hooks run outside the project env.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Any

EDIT_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit"}
# Redirects that write nowhere (2>&1, > /dev/null) must not make a read-only command look
# like a write, or the lock becomes so noisy that people unlock it habitually.
HARMLESS_REDIRECT = re.compile(r"(&|\d*)>&\d+|(&|\d*)>>?\s*/dev/null(?![\w./-])")
_NOT_CHAINED = r"[^|;&]*"  # stay within one shell command
WRITE_PATTERN = re.compile(
    r"(>|\btee\b|\bsed\s+-i|\brm\b|\bmv\b|\bcp\b|\bchmod\b|\bchown\b|\btruncate\b|\bdd\b"
    r"|\btouch\b|\bpatch\b|\brsync\b|\bwget\b|\bln\b|\binstall\b|\bawk\s+-i\b|\bperl\s+-\w*i"
    rf"|\bcurl\b{_NOT_CHAINED}\s-[a-z]*o\b"
    rf"|\bgit\b{_NOT_CHAINED}\s(checkout|restore|reset|clean|stash|apply|am)\b"
    rf"|\b(python3?|node|perl|ruby)\b{_NOT_CHAINED}(\s-[ce]\b|<<))",
    re.IGNORECASE,
)


def load_protected(listing: Path) -> list[str]:
    entries = []
    for raw in listing.read_text().splitlines():
        line = raw.strip()
        if line and not line.startswith("#"):
            entries.append(line)
    return entries


def _covers(entry: str, relative: str) -> bool:
    # Case-insensitive: the owner's macOS filesystem treats Harness/ and harness/ as one.
    entry, relative = entry.casefold(), relative.casefold()
    if entry.endswith("/"):
        base = entry.rstrip("/")
        return relative == base or relative.startswith(base + "/")
    return relative == entry


def _path_hit(raw: str, protected: list[str], project_dir: Path) -> str | None:
    candidate = Path(raw)
    if not candidate.is_absolute():
        candidate = project_dir / candidate
    resolved = Path(os.path.realpath(candidate))
    root = Path(os.path.realpath(project_dir))
    try:
        relative = resolved.relative_to(root).as_posix()
    except ValueError:
        return None
    for entry in protected:
        if _covers(entry, relative):
            return entry
    return None


def _mentions(command: str, entry: str) -> bool:
    name = re.escape(entry.rstrip("/"))
    pattern = rf"(?<![\w.-]){name}(?:/|(?![\w.-]))"
    return re.search(pattern, command, re.IGNORECASE) is not None


def decide(
    payload: dict[str, Any], protected: list[str], project_dir: Path, unlocked: bool
) -> tuple[bool, str]:
    """Return (blocked, message)."""
    if unlocked or not protected:
        return False, ""
    tool = payload.get("tool_name")
    if tool not in EDIT_TOOLS and tool != "Bash":
        return False, ""
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        return True, f"{tool} call has malformed tool_input; failing closed"
    if tool in EDIT_TOOLS:
        raw = tool_input.get("file_path") or tool_input.get("notebook_path")
        if raw:
            entry = _path_hit(str(raw), protected, project_dir)
            if entry:
                return True, f"{raw} is protected ({entry})"
        return False, ""
    command = str(tool_input.get("command") or "")
    if WRITE_PATTERN.search(HARMLESS_REDIRECT.sub("", command)):
        for entry in protected:
            if _mentions(command, entry):
                return True, f"command may write to a protected path ({entry})"
    return False, ""


def _project_dir() -> Path:
    from_env = os.environ.get("CLAUDE_PROJECT_DIR")
    # Not cwd: an agent running from a subdirectory must not change which list is read.
    return Path(from_env) if from_env else Path(__file__).resolve().parents[2]


def main() -> int:
    try:
        payload = json.loads(sys.stdin.read())
        if not isinstance(payload, dict):
            raise ValueError("payload is not an object")
        project_dir = _project_dir()
        listing = project_dir / ".claude" / "protected_paths.txt"
        if not listing.exists():
            raise FileNotFoundError(f"{listing} is missing")
        unlocked = os.environ.get("LAB_UNLOCK_PROTECTED") == "1"
        blocked, message = decide(payload, load_protected(listing), project_dir, unlocked)
    except Exception as exc:  # fail closed on anything unexpected
        sys.stderr.write(f"protect_paths: failing closed: {type(exc).__name__}: {exc}\n")
        return 2
    if blocked:
        sys.stderr.write(
            f"BLOCKED: {message}. See docs/PROTECTIONS.md; the owner can unlock by "
            "launching with LAB_UNLOCK_PROTECTED=1.\n"
        )
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
