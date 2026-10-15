import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
HOOK = Path(sys.executable).parent / "detect-secrets-hook"
BASELINE = REPO / ".secrets.baseline"


def scan(path: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(HOOK), "--baseline", str(BASELINE), str(path)],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=False,
    )


def test_planted_private_key_is_flagged(tmp_path: Path) -> None:
    # Built at runtime so this file itself contains no secret-looking literal.
    header = "-----BEGIN " + "RSA PRIVATE KEY-----"
    footer = "-----END " + "RSA PRIVATE KEY-----"
    planted = tmp_path / "leak.txt"
    planted.write_text(f"{header}\nMIIEowIBAAKCAQEA0123456789\n{footer}\n")
    result = scan(planted)
    assert result.returncode != 0


def test_clean_file_passes(tmp_path: Path) -> None:
    clean = tmp_path / "clean.txt"
    clean.write_text("nothing secret here\n")
    assert scan(clean).returncode == 0


def test_env_example_has_no_values() -> None:
    for line in (REPO / ".env.example").read_text().splitlines():
        if line.startswith(("LAB_BROKER_API_KEY=", "LAB_BROKER_API_SECRET=")):
            assert line.endswith("="), line
