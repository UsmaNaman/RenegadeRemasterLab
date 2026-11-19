#!/usr/bin/env python3
"""Audit committed snapshots and commit metadata reachable from local refs and HEAD.

Run from the repository with python scripts/release_audit.py. Rows identify a category,
commit, path and line (a dash for whole-file findings). Matched contents are omitted.
Exit 0 for clean history, 1 for findings, or 2 if the scan could not finish.
"""

from __future__ import annotations

import functools
import itertools
import json
import re
import subprocess
import sys
from collections.abc import Iterable
from pathlib import PurePosixPath

# Keep direct CLI use read-only even when the hook has no cached bytecode.
sys.dont_write_bytecode = True

from check_no_game_data import FOURCC_AT_0, HEAD_BYTES, MAX_BYTES, SIGNATURES, TIM_MAGIC  # noqa: E402

GIT = ["git", "--no-optional-locks", "--no-replace-objects", "--no-lazy-fetch"]
PATTERNS = {
    "home-path": rb"(?:/Users/|/home/)[\w.~-]+",
    "email": rb"(?<![\w.%+-])[\w.%+-]+@[\w.-]+\.[A-Za-z]{2,}",
    "secret": (
        rb"-----BEGIN (?:[A-Z0-9 ]+ )?PRIVATE KEY-----"
        rb"|\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[\w]{20,}|AKIA[A-Z0-9]{16}"
        rb"|sk-[\w-]{20,}|xox[baprs]-[\w-]{10,})"
        rb"""|(?i:\b(?:api[_-]?key|access[_-]?token|secret[_-]?key|password|token)["']?"""
        rb"""\s*[:=]\s*["']?[A-Za-z0-9_+/=.~-]{8,})"""
    ),
}
RULES = {name: re.compile(pattern) for name, pattern in PATTERNS.items()}
type Hit = tuple[str, int | None]


def git(*args: str) -> bytes:
    return subprocess.check_output([*GIT, *args])


def text_hits(lines: Iterable[bytes]) -> Iterable[Hit]:
    for number, line in enumerate(lines, 1):
        for category, rule in RULES.items():
            if rule.search(line):
                yield category, number


@functools.cache
def blob_hits(oid: str, tim: bool) -> list[Hit]:
    size = int(git("cat-file", "-s", oid))
    with subprocess.Popen([*GIT, "cat-file", "blob", oid], stdout=subprocess.PIPE) as proc:
        assert proc.stdout is not None
        head = proc.stdout.read(HEAD_BYTES)
        if len(head) != min(size, HEAD_BYTES):
            raise RuntimeError("incomplete Git blob prefix")
        hits: list[Hit] = [
            ("game-magic", None)
            for offset, magic, _ in SIGNATURES
            if head.startswith(magic, offset)
        ]
        if (b"\0" in head[:512] and head[:4] in FOURCC_AT_0) or (
            tim and head.startswith(TIM_MAGIC)
        ):
            hits.append(("game-magic", None))
        if hits or b"\0" in head[:8000]:
            if size > MAX_BYTES:
                hits.append(("large-binary", None))
            # Binary checks need only the prefix. Closing the pipe stops Git on large blobs.
            proc.stdout.close()
            return hits
        # Keep the line crossing the prefix boundary intact.
        first, sep, tail = head.rpartition(b"\n")
        if sep:
            hits.extend(text_hits(first.split(b"\n")))
        offset = head.count(b"\n")

        lines = itertools.chain([tail + proc.stdout.readline()], proc.stdout)
        hits.extend((kind, offset + n) for kind, n in text_hits(lines) if n is not None)
        if proc.wait():
            raise RuntimeError("could not finish reading a Git blob")
        return hits


def main() -> int:
    refs = ["--all"]
    if (
        subprocess.run(
            [*GIT, "rev-parse", "--verify", "--quiet", "HEAD"], stdout=subprocess.DEVNULL
        ).returncode
        == 0
    ):
        refs.append("HEAD")
    found = False
    print("finding\tcommit\tpath\tline")
    for raw in git("rev-list", *refs).splitlines():
        commit = raw.decode("ascii")

        def report(path: str, hits: Iterable[Hit], commit: str = commit) -> None:
            nonlocal found
            for category, line in hits:
                found = True
                print(category, commit, json.dumps(path), line or "-", sep="\t")

        report("<commit>", text_hits(git("cat-file", "commit", commit).split(b"\n")))
        for record in git("ls-tree", "-rz", "--full-tree", commit).split(b"\0"):
            if not record:
                continue
            metadata, path = record.split(b"\t", 1)
            _, kind, oid = metadata.split()
            name = path.decode("utf-8", "surrogateescape")
            report(name, ((k, None) for k, _ in text_hits([path])))
            if kind == b"blob":
                report(
                    name,
                    blob_hits(oid.decode("ascii"), PurePosixPath(name).suffix.lower() == ".tim"),
                )
    return int(found)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"audit incomplete: {exc}", file=sys.stderr)
        sys.exit(2)
