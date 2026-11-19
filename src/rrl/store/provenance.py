"""Record every operation RRL performs, whether it succeeds or fails."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from rrl import __version__

NOW = "strftime('%Y-%m-%dT%H:%M:%fZ', 'now')"


@contextmanager
def operation(
    conn: sqlite3.Connection,
    op: str,
    *,
    source_id: int | None = None,
    adapter_id: str | None = None,
    adapter_version: str | None = None,
    params: dict[str, Any] | None = None,
) -> Iterator[int]:
    with conn:
        cur = conn.execute(
            "INSERT INTO operation (op, source_id, adapter_id, adapter_version, rrl_version,"
            " params_json, status) VALUES (?, ?, ?, ?, ?, ?, 'running')",
            (
                op,
                source_id,
                adapter_id,
                adapter_version,
                __version__,
                json.dumps(params or {}, sort_keys=True),
            ),
        )
    op_id = cur.lastrowid
    assert op_id is not None
    try:
        yield op_id
    except BaseException as exc:
        with conn:
            conn.execute(
                f"UPDATE operation SET status = 'failed', error = ?, finished_at = {NOW}"
                " WHERE id = ?",
                (f"{type(exc).__name__}: {exc}", op_id),
            )
        raise
    with conn:
        conn.execute(
            f"UPDATE operation SET status = 'succeeded', finished_at = {NOW} WHERE id = ?",
            (op_id,),
        )
