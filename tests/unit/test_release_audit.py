"""Exercise the release audit against disposable Git histories."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts/release_audit.py"


def git(repo: Path, *args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=repo, text=True).strip()


def audit(repo: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-B", str(SCRIPT)], cwd=repo, capture_output=True, text=True, check=False
    )


def test_history_audit(tmp_path: Path) -> None:
    git(tmp_path, "init", "-b", "main")
    git(tmp_path, "config", "user.name", "Tester")
    git(tmp_path, "config", "user.email", "tester@localhost")
    git(tmp_path, "-c", "commit.gpgsign=false", "commit", "--allow-empty", "-m", "clean")
    assert audit(tmp_path).returncode == 0
    git(tmp_path, "switch", "-c", "archive")
    private = "/Users/" + "synthetic"
    email = "synthetic" + "@example.invalid"
    token = "ghp_" + "x" * 36
    (tmp_path / "notes").write_text("intro\n" + "\n".join([private, email, token]))
    data = bytearray(1024 * 1024 + 1)
    data[0x8001:0x8006] = b"CD001"
    (tmp_path / "payload").write_bytes(data)
    (tmp_path / "large-text").write_text("x" * len(data))
    (tmp_path / "image.TIM").write_bytes(bytes([16, 0, 0, 0]))
    git(tmp_path, "add", ".")
    git(tmp_path, "-c", "commit.gpgsign=false", "commit", "-m", "synthetic")
    old = git(tmp_path, "rev-parse", "HEAD")
    git(tmp_path, "rm", "notes")
    git(tmp_path, "-c", "commit.gpgsign=false", "commit", "-m", "removed")
    git(tmp_path, "switch", "main")
    (tmp_path / "untracked").write_text(token)
    refs = git(tmp_path, "show-ref")
    result = audit(tmp_path)
    assert result.returncode == 1, result.stderr
    for category, path, line in [
        ("home-path", "notes", "2"),
        ("email", "notes", "3"),
        ("secret", "notes", "4"),
        ("game-magic", "payload", "-"),
        ("large-binary", "payload", "-"),
        ("game-magic", "image.TIM", "-"),
    ]:
        assert f'{category}\t{old}\t"{path}"\t{line}' in result.stdout
    for value in (private, email, token, "untracked", "large-text"):
        assert value not in result.stdout
    assert git(tmp_path, "show-ref") == refs
    assert (tmp_path / "untracked").read_text() == token
