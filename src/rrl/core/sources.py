"""Registering original game images. RRL hashes and references them; it never writes to them."""

from __future__ import annotations

import logging
import os
import sqlite3
from pathlib import Path

from rrl.api import Match, Source
from rrl.core.hashing import sha256_file
from rrl.store.provenance import operation

log = logging.getLogger(__name__)


class SourceError(Exception):
    pass


def _to_source(row: sqlite3.Row) -> Source:
    return Source(path=Path(row["path"]), sha256=row["sha256"], size=row["size"])


def add_source(conn: sqlite3.Connection, path: Path) -> tuple[int, Source]:
    """Register an image, returning (source id, Source). Re-adding is cheap and idempotent."""
    path = path.expanduser().resolve()
    if not path.is_file():
        raise SourceError(f"not a file: {path}")
    st = path.stat()

    # Skip re-hashing when nothing about the file has changed.
    row = conn.execute(
        "SELECT * FROM source WHERE path = ? AND size = ? AND mtime_ns = ?",
        (str(path), st.st_size, st.st_mtime_ns),
    ).fetchone()
    if row is not None:
        return row["id"], _to_source(row)

    if os.access(path, os.W_OK):
        log.warning("%s is writable; consider `chmod a-w` to protect the original", path.name)

    with operation(conn, "source.add", params={"path": str(path)}):
        digest = sha256_file(path)
        with conn:
            conn.execute(
                "INSERT INTO source (path, sha256, size, mtime_ns) VALUES (?, ?, ?, ?)"
                " ON CONFLICT (sha256) DO UPDATE SET path = excluded.path,"
                " mtime_ns = excluded.mtime_ns",
                (str(path), digest, st.st_size, st.st_mtime_ns),
            )
        row = conn.execute("SELECT * FROM source WHERE sha256 = ?", (digest,)).fetchone()
    return row["id"], _to_source(row)


def get_source(conn: sqlite3.Connection, ref: str) -> tuple[int, Source]:
    """Resolve a source by numeric id, or register it if ref is a path."""
    if ref.isdigit():
        row = conn.execute("SELECT * FROM source WHERE id = ?", (int(ref),)).fetchone()
        if row is None:
            raise SourceError(f"no source with id {ref}")
        if not Path(row["path"]).is_file():
            raise SourceError(f"source {ref} has moved or been deleted: {row['path']}")
        return row["id"], _to_source(row)
    return add_source(conn, Path(ref))


def record_identity(conn: sqlite3.Connection, source_id: int, match: Match) -> None:
    with conn:
        conn.execute(
            "UPDATE source SET platform = ?, title = ?, title_id = ?, region = ?, adapter_id = ?"
            " WHERE id = ?",
            (
                match.platform,
                match.title,
                match.title_id,
                match.region,
                match.adapter_id,
                source_id,
            ),
        )
