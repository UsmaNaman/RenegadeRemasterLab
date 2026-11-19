"""Write-once content-addressed blob store: <root>/ab/cd/<sha256>, files made read-only."""

from __future__ import annotations

import hashlib
import os
import sqlite3
import tempfile
from pathlib import Path


def blob_path(root: Path, sha256: str) -> Path:
    return root / sha256[:2] / sha256[2:4] / sha256


def put_bytes(
    conn: sqlite3.Connection, root: Path, data: bytes, media_type: str | None = None
) -> str:
    """Store data, returning its sha256. Storing identical bytes twice is a no-op."""
    sha256 = hashlib.sha256(data).hexdigest()
    dest = blob_path(root, sha256)
    if not dest.exists():
        dest.parent.mkdir(parents=True, exist_ok=True)
        # Write to a temp file then rename, so a crash never leaves a partial blob.
        fd, tmp = tempfile.mkstemp(dir=dest.parent)
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(data)
            os.chmod(tmp, 0o444)
            try:
                os.rename(tmp, dest)
            except FileExistsError:
                # Windows refuses to rename over a blob published by a concurrent writer.
                if not dest.is_file():
                    raise
        finally:
            if os.path.exists(tmp):
                os.chmod(tmp, 0o600)  # Windows cannot unlink a read-only temporary file.
                os.unlink(tmp)
    with conn:
        conn.execute(
            "INSERT OR IGNORE INTO blob (sha256, size, media_type) VALUES (?, ?, ?)",
            (sha256, len(data), media_type),
        )
    return sha256
