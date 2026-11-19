#!/usr/bin/env python3
"""Prepare one audited public commit from the private master snapshot."""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

AUDIT = Path(__file__).with_name("release_audit.py").resolve()
MAIN = "refs/remotes/origin/main"
RELEASE = "refs/heads/release"


def git(*args: str, data: str | None = None) -> str:
    return subprocess.check_output(
        ["git", "--no-optional-locks", "--no-replace-objects", *args], input=data, text=True
    ).strip()


def publish(args: argparse.Namespace, scratch: Path) -> None:
    if git("status", "--porcelain", "--untracked-files=all", "--ignore-submodules=none"):
        raise ValueError("working tree is dirty")
    identity = args.identity or git("config", "--get", "rrl.publishIdentity")
    match = re.fullmatch(r"([^<>\r\n]+) <([^<>\s@]+@users\.noreply\.github\.com)>", identity)
    if not match or not match[1].strip():
        raise ValueError("expected Name <email> with a GitHub noreply address")
    for role in ("AUTHOR", "COMMITTER"):
        os.environ[f"GIT_{role}_NAME"] = match[1].strip()
        os.environ[f"GIT_{role}_EMAIL"] = match[2]
    master = git("rev-parse", "--verify", "refs/heads/master")
    tree = git("rev-parse", master + "^{tree}")
    if git("for-each-ref", "--format=%(symref)", MAIN, RELEASE):
        raise ValueError("release and origin/main must be direct refs")
    old_release = git("for-each-ref", "--format=%(objectname)", RELEASE)
    if f"branch {RELEASE}" in git("worktree", "list", "--porcelain").splitlines():
        raise ValueError("release is checked out in a worktree")
    tag_ref = None
    if args.tag is not None:
        tag_ref = "refs/tags/" + args.tag
        git("check-ref-format", tag_ref)
        if git("for-each-ref", "--format=%(refname)", tag_ref):
            raise ValueError("tag already exists")

    fetch = ["fetch", "--no-tags", "--no-write-fetch-head", "--no-auto-maintenance", "--refmap="]
    if args.dry_run:
        # All preview objects, including a newly fetched public parent, disappear on exit.
        objects = git("rev-parse", "--path-format=absolute", "--git-path", "objects")
        os.environ["GIT_ALTERNATE_OBJECT_DIRECTORIES"] = objects
        os.environ["GIT_OBJECT_DIRECTORY"] = str(scratch)
        parent = git("ls-remote", "--exit-code", "origin", "refs/heads/main").split()[0]
        git(*fetch, "origin", parent)
    else:
        git(*fetch, "origin", "refs/heads/main:" + MAIN)
        parent = git("rev-parse", MAIN)

    base = git("rev-parse", parent + "^{tree}")
    if base not in git("log", "--format=%T", master).splitlines():
        raise ValueError("origin/main tree does not match any snapshot reachable from master")

    candidate = git("commit-tree", tree, "-p", parent, "-m", args.message)
    print(f"release: {candidate}, tree {tree}, sole parent {parent}", flush=True)
    # Audit the immutable candidate before exposing it through release or a tag. Commits
    # already public cannot change, so only the new snapshot is checked.
    audit = subprocess.run(
        [sys.executable, "-B", str(AUDIT), "--ref", candidate, "--since", parent, "--publish"]
    )
    if audit.returncode:
        raise ValueError("release audit failed")
    print("Release audit passed.")
    updates = [
        f"verify refs/heads/master {master}",
        f"update {RELEASE} {candidate} {old_release or '0' * len(master)}",
    ]
    if tag_ref:
        tagger = git("var", "GIT_COMMITTER_IDENT")
        tag_data = (
            f"object {candidate}\ntype commit\ntag {args.tag}\ntagger {tagger}\n\n{args.tag}\n"
        )
        tag_oid = git("mktag", data=tag_data)
        updates.append(f"create {tag_ref} {tag_oid}")
    if args.dry_run:
        print("Dry run: would update release" + (f" and create {tag_ref}" if tag_ref else ""))
        if args.push:
            print("Would push release to origin/main" + (f" and {tag_ref}" if tag_ref else ""))
        return
    git("update-ref", "--no-deref", "--stdin", data="\n".join(updates) + "\n")
    if args.push:
        # Pin both sources to the objects we just audited and created. Another
        # publisher moving a local ref cannot change what this push sends.
        specs = [f"{candidate}:refs/heads/main"]
        if tag_ref:
            specs.append(f"{tag_oid}:{tag_ref}")
        git(
            "-c",
            "remote.origin.mirror=false",
            "push",
            "--atomic",
            "--no-follow-tags",
            "--recurse-submodules=no",
            "origin",
            *specs,
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--identity", help="defaults to git config rrl.publishIdentity")
    parser.add_argument("--message", default="Update public release")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--push", action="store_true")
    parser.add_argument("--tag")
    args = parser.parse_args()
    try:
        with tempfile.TemporaryDirectory() as scratch:
            publish(args, Path(scratch))
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        print(f"publish refused: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
