import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

REPO = Path(__file__).resolve().parents[2]
HOOK_PATH = REPO / ".claude" / "hooks" / "protect_paths.py"


def load_hook() -> ModuleType:
    spec = importlib.util.spec_from_file_location("protect_paths", HOOK_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


hook = load_hook()
PROTECTED = ["harness/", "contracts/", ".github/", "pyproject.toml"]
PROJECT = Path("/proj")


def decide(tool: str, tool_input: dict[str, str], *, unlocked: bool = False) -> bool:
    """True means blocked."""
    blocked, _ = hook.decide(
        {"tool_name": tool, "tool_input": tool_input}, PROTECTED, PROJECT, unlocked
    )
    return bool(blocked)


@pytest.mark.parametrize("tool", ["Write", "Edit", "MultiEdit", "NotebookEdit"])
def test_edit_tools_blocked_inside_protected_directory(tool: str) -> None:
    key = "notebook_path" if tool == "NotebookEdit" else "file_path"
    assert decide(tool, {key: "/proj/harness/engine/x.py"})


def test_relative_and_exact_file_entries_are_blocked() -> None:
    assert decide("Edit", {"file_path": "contracts/intent.py"})
    assert decide("Edit", {"file_path": "/proj/pyproject.toml"})


def test_dotdot_traversal_is_resolved() -> None:
    assert decide("Edit", {"file_path": "/proj/strategies/../harness/x.py"})


def test_unprotected_paths_are_allowed() -> None:
    assert not decide("Edit", {"file_path": "/proj/strategies/baseline.py"})
    assert not decide("Write", {"file_path": "/proj/harness_notes.md"})
    assert not decide("Edit", {"file_path": "/elsewhere/harness/x.py"})


@pytest.mark.parametrize(
    "command",
    [
        "echo x > contracts/a.py",
        "echo x >> harness/a.py",
        "sed -i '' s/a/b/ harness/a.py",
        "rm -rf .github/workflows",
        "mv contracts/a.py /tmp/a.py",
        "cp /tmp/a.py harness/a.py",
        "chmod +w harness/a.py",
        "git checkout -- contracts/intent.py",
        "tee pyproject.toml",
        "touch harness/x.py",
        "cd harness && touch x",
        "patch -p1 harness/a.py < fix.diff",
        "rsync -a /tmp/new/ harness/",
        "curl -o harness/a.py https://example.com/a",
        "wget -O contracts/a.py https://example.com/a",
        "git -C . checkout -- harness",
        "awk -i inplace '{print}' harness/a.py",
        "python3 -c \"open('harness/a.py','w').write('x')\"",
        "python - <<EOF\nopen('pyproject.toml','w')\nEOF",
        "node -e \"require('fs').writeFileSync('contracts/a.py','x')\"",
    ],
)
def test_bash_writes_to_protected_paths_are_blocked(command: str) -> None:
    assert decide("Bash", {"command": command})


@pytest.mark.parametrize(
    "command",
    [
        "cat contracts/intent.py",
        "git diff harness",
        "uv run pytest tests/unit",
        "ls harness",
        "echo hello > scratch.txt",
        "sed -i '' s/a/b/ strategies/x.py",
        "diff harness/a.py b.py 2>&1",
        "rg x contracts/ 2>/dev/null",
        "git log --oneline contracts/ -n 3 2>&1",
        "head -5 pyproject.toml 2>/dev/null",
        "cat contracts/intent.py > /dev/null",
        "uv run pytest tests/guards 2>&1 | tail -5",
        "cat harness/a.py &>/dev/null",
    ],
)
def test_bash_reads_and_unrelated_writes_are_allowed(command: str) -> None:
    assert not decide("Bash", {"command": command})


def test_redirect_to_a_real_file_is_still_blocked_next_to_a_harmless_one() -> None:
    assert decide("Bash", {"command": "cat x 2>&1 > harness/a.py"})


@pytest.mark.parametrize(
    "command",
    [
        "echo x >/dev/nullx harness",
        "echo x > /dev/null/../../harness/a",
    ],
)
def test_dev_null_lookalikes_are_not_treated_as_harmless(command: str) -> None:
    assert decide("Bash", {"command": command})


def test_unlock_flag_allows_everything() -> None:
    assert not decide("Edit", {"file_path": "/proj/harness/x.py"}, unlocked=True)
    assert not decide("Bash", {"command": "rm -rf harness"}, unlocked=True)


def test_empty_protected_list_blocks_nothing() -> None:
    blocked, _ = hook.decide(
        {"tool_name": "Edit", "tool_input": {"file_path": "/proj/harness/x.py"}},
        [],
        PROJECT,
        False,
    )
    assert not blocked


def test_other_tools_are_ignored() -> None:
    assert not decide("Read", {"file_path": "/proj/harness/x.py"})


def test_protected_list_file_ignores_comments_and_blanks(tmp_path: Path) -> None:
    listing = tmp_path / "protected_paths.txt"
    listing.write_text("# comment\n\nharness/\n  contracts/  \n")
    assert hook.load_protected(listing) == ["harness/", "contracts/"]
    with pytest.raises(FileNotFoundError):
        hook.load_protected(tmp_path / "missing.txt")


def run_hook(
    payload: object, project: Path, protected: str, **env: str
) -> subprocess.CompletedProcess[str]:
    (project / ".claude").mkdir(exist_ok=True)
    (project / ".claude" / "protected_paths.txt").write_text(protected)
    full_env = {"CLAUDE_PROJECT_DIR": str(project), "PATH": "/usr/bin:/bin", **env}
    return subprocess.run(
        [sys.executable, str(HOOK_PATH)],
        input=payload if isinstance(payload, str) else json.dumps(payload),
        capture_output=True,
        text=True,
        env=full_env,
        check=False,
    )


def test_script_blocks_with_exit_code_two(tmp_path: Path) -> None:
    payload = {"tool_name": "Edit", "tool_input": {"file_path": str(tmp_path / "harness" / "x.py")}}
    result = run_hook(payload, tmp_path, "harness/\n")
    assert result.returncode == 2
    assert "harness/" in result.stderr


def test_script_allows_with_exit_code_zero(tmp_path: Path) -> None:
    payload = {"tool_name": "Edit", "tool_input": {"file_path": str(tmp_path / "ok" / "x.py")}}
    assert run_hook(payload, tmp_path, "harness/\n").returncode == 0


def test_script_honours_unlock_env(tmp_path: Path) -> None:
    payload = {"tool_name": "Edit", "tool_input": {"file_path": str(tmp_path / "harness" / "x.py")}}
    result = run_hook(payload, tmp_path, "harness/\n", LAB_UNLOCK_PROTECTED="1")
    assert result.returncode == 0


def test_script_fails_closed_on_malformed_input(tmp_path: Path) -> None:
    assert run_hook("not json", tmp_path, "harness/\n").returncode == 2


@pytest.mark.parametrize(
    "path",
    [
        "/proj/Harness/a.py",
        "/proj/HARNESS/new.py",
        "/proj/Contracts/Intent.py",
        "/proj/PyProject.toml",
    ],
)
def test_case_variants_are_blocked(path: str) -> None:
    assert decide("Write", {"file_path": path})


def test_case_variants_in_bash_are_blocked() -> None:
    assert decide("Bash", {"command": "touch HARNESS/x"})


@pytest.mark.parametrize("bad_input", ["x", ["a"], None, 5])
def test_non_dict_tool_input_is_blocked_when_a_lock_is_active(bad_input: object) -> None:
    blocked, _ = hook.decide(
        {"tool_name": "Edit", "tool_input": bad_input}, PROTECTED, PROJECT, False
    )
    assert blocked


def test_script_fails_closed_when_the_list_is_missing(tmp_path: Path) -> None:
    payload = {"tool_name": "Edit", "tool_input": {"file_path": str(tmp_path / "ok.py")}}
    full_env = {"CLAUDE_PROJECT_DIR": str(tmp_path), "PATH": "/usr/bin:/bin"}
    result = subprocess.run(
        [sys.executable, str(HOOK_PATH)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env=full_env,
        check=False,
    )
    assert result.returncode == 2
    assert "protected_paths.txt" in result.stderr


def test_script_fails_closed_on_unexpected_errors(tmp_path: Path) -> None:
    payload = {"tool_name": "Edit", "tool_input": {"file_path": "bad\x00path"}}
    assert run_hook(payload, tmp_path, "harness/\n").returncode == 2


def test_project_root_comes_from_the_hook_location_not_cwd(tmp_path: Path) -> None:
    hooks = tmp_path / ".claude" / "hooks"
    hooks.mkdir(parents=True)
    (hooks / "protect_paths.py").write_text(HOOK_PATH.read_text())
    (tmp_path / ".claude" / "protected_paths.txt").write_text("harness/\n")
    sub = tmp_path / "some" / "subdir"
    sub.mkdir(parents=True)
    payload = {"tool_name": "Edit", "tool_input": {"file_path": str(tmp_path / "harness" / "x.py")}}
    result = subprocess.run(
        [sys.executable, str(hooks / "protect_paths.py")],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        cwd=sub,
        env={"PATH": "/usr/bin:/bin"},
        check=False,
    )
    assert result.returncode == 2


def test_shipped_settings_wire_the_hook_to_every_write_tool() -> None:
    settings = json.loads((REPO / ".claude" / "settings.json").read_text())
    entries = settings["hooks"]["PreToolUse"]
    assert len(entries) == 1
    matcher = entries[0]["matcher"].split("|")
    assert {"Write", "Edit", "MultiEdit", "NotebookEdit", "Bash"} <= set(matcher)
    command = entries[0]["hooks"][0]["command"]
    assert "protect_paths.py" in command
    assert entries[0]["hooks"][0]["type"] == "command"


REQUIRED_AT_ACTIVATION = {
    "contracts/",
    ".github/",
    ".claude/",
    "pyproject.toml",
    "tools/",
    "engine/",
    "tests/contract_suite/",
    "tests/guards/",
    "docs/PROTECTIONS.md",
    "harness/",
}


def shipped_activation_entries() -> list[str]:
    text = (REPO / ".claude" / "protected_paths.txt").read_text()
    return [line[1:].strip() for line in text.splitlines() if line.startswith("#   ")]


def test_documented_activation_list_covers_the_guards() -> None:
    assert set(shipped_activation_entries()) >= REQUIRED_AT_ACTIVATION


def test_activating_the_documented_list_blocks_real_paths(tmp_path: Path) -> None:
    entries = "\n".join(shipped_activation_entries()) + "\n"
    for entry in shipped_activation_entries():
        target = tmp_path / entry.rstrip("/") / ("x.py" if entry.endswith("/") else "")
        payload = {"tool_name": "Write", "tool_input": {"file_path": str(target)}}
        result = run_hook(payload, tmp_path, entries)
        assert result.returncode == 2, entry
