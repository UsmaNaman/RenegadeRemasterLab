"""Exercise the publisher with independent private and public histories."""

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PUBLIC = "123+painter@users.noreply.github.com"
PRIVATE = "dev" + "@example.org"


def git(repo: Path, *args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=repo, text=True).strip()


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "no-global"))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    public = tmp_path / "public"
    public.mkdir()
    git(public, "init", "-b", "main")
    git(public, "config", "user.name", "Public")
    git(public, "config", "user.email", PUBLIC)
    git(public, "commit", "--allow-empty", "-m", "public")
    origin = tmp_path / "origin.git"
    git(tmp_path, "clone", "--bare", str(public), str(origin))
    local = tmp_path / "local"
    local.mkdir()
    git(local, "init", "-b", "master")
    git(local, "config", "user.name", "Private")
    git(local, "config", "user.email", PRIVATE)
    git(local, "config", "rrl.publishIdentity", f"Painter <{PUBLIC}>")
    git(local, "remote", "add", "origin", str(origin))
    git(local, "commit", "--allow-empty", "-m", "private start")
    patch = local / "patches/xemu/demo.patch"
    patch.parent.mkdir(parents=True)
    patch.write_text(
        "From: Painter <" + PRIVATE + ">\n\nCo-Authored-By: Claude <noreply@anthropic.com>\n"
    )
    git(local, "add", ".")
    git(local, "commit", "-m", "private change")
    return local


def publish(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-B", str(ROOT / "scripts/publish.py"), *args],
        cwd=repo,
        capture_output=True,
        text=True,
    )


def test_publish(repo: Path) -> None:
    master = git(repo, "rev-parse", "master")
    result = publish(repo, "--tag", "v1", "--message", "Public changes")
    assert result.returncode == 0, result.stderr
    assert git(repo, "show", "-s", "--format=%P", "release") == git(
        repo, "rev-parse", "origin/main"
    )
    assert git(repo, "rev-parse", "master^{tree}") == git(repo, "rev-parse", "release^{tree}")
    assert (
        git(repo, "show", "-s", "--format=%ae %ce %s", "release")
        == f"{PUBLIC} {PUBLIC} Public changes"
    )
    assert git(repo, "rev-parse", "v1^{commit}") == git(repo, "rev-parse", "release")
    assert git(repo, "for-each-ref", "--format=%(taggeremail)", "refs/tags/v1") == f"<{PUBLIC}>"
    assert git(repo, "rev-parse", "master") == master


def test_preview(repo: Path) -> None:
    refs = git(repo, "show-ref")
    objects = git(repo, "count-objects", "-v")
    result = publish(repo, "--dry-run", "--push", "--tag", "v1")
    assert result.returncode == 0, result.stderr
    assert "Release audit passed" in result.stdout
    assert git(repo, "show-ref") == refs
    assert git(repo, "count-objects", "-v") == objects


def test_refusals(repo: Path) -> None:
    assert "noreply" in publish(repo, "--identity", f"Tester <{PRIVATE}>").stderr
    (repo / "untracked").touch()
    assert "dirty" in publish(repo).stderr
    assert git(repo, "branch", "--list", "release") == ""


def test_unrelated_tree(repo: Path) -> None:
    git(repo, "switch", "--orphan", "other")
    (repo / "new").touch()
    git(repo, "add", ".")
    git(repo, "commit", "-m", "other root")
    git(repo, "branch", "-M", "master")
    assert "snapshot" in publish(repo).stderr


@pytest.mark.parametrize(
    "path,line",
    [
        ("readme", f"From: {PRIVATE}"),
        ("patches/xemu/demo.patch", f"Cc: {PUBLIC}, {PRIVATE}"),
        ("notes", "ghp_" + "x" * 24),
    ],
)
def test_audit_blocks_push(repo: Path, path: str, line: str) -> None:
    origin = repo.parent / "origin.git"
    before = git(origin, "show-ref")
    (repo / path).write_text(line)
    git(repo, "add", ".")
    git(repo, "commit", "-m", "should fail")
    result = publish(repo, "--push", "--tag", "v1")
    assert result.returncode and "audit failed" in result.stderr
    assert PRIVATE not in result.stdout
    assert git(origin, "show-ref") == before
    assert git(repo, "branch", "--list", "release") == ""


def test_hook(repo: Path) -> None:
    assert publish(repo, "--tag", "v1").returncode == 0
    for ref, accepted in [("refs/heads/master", False), ("refs/tags/v1", True)]:
        oid = git(repo, "rev-parse", ref)
        result = subprocess.run(
            ["sh", str(ROOT / ".githooks/pre-push")],
            cwd=repo,
            text=True,
            input=f"{ref} {oid} refs/heads/main {'0' * len(oid)}\n",
        )
        assert (result.returncode == 0) == accepted


def test_public_history_is_not_reaudited(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # A finding already published cannot be withdrawn; it must not block a clean update.
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "no-global"))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    local = tmp_path / "local"
    local.mkdir()
    git(local, "init", "-b", "master")
    git(local, "config", "user.name", "Painter")
    git(local, "config", "user.email", PUBLIC)
    git(local, "config", "rrl.publishIdentity", f"Painter <{PUBLIC}>")
    (local / "notes.txt").write_text("see /" + "home/alice\n")
    git(local, "add", ".")
    git(local, "commit", "-m", "old public snapshot")
    origin = tmp_path / "origin.git"
    git(tmp_path, "clone", "--bare", str(local), str(origin))
    git(local, "remote", "add", "origin", str(origin))
    git(local, "push", "-q", "origin", "master:main")
    (local / "notes.txt").write_text("clean\n")
    git(local, "commit", "-am", "cleanup")
    result = publish(local, "--message", "Update")
    assert result.returncode == 0, result.stdout + result.stderr
    (local / "notes.txt").write_text("see /" + "home/alice again\n")
    git(local, "commit", "-am", "regression")
    assert publish(local, "--message", "Update 2").returncode == 1
