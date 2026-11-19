"""Run an adapter's extract(): store every asset in the blob store with provenance."""

from __future__ import annotations

import json
import logging
import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from rrl.api import Adapter, Asset, Extractor, Source
from rrl.store.blobs import blob_path, put_bytes
from rrl.store.provenance import operation

log = logging.getLogger(__name__)


class ExtractionError(Exception):
    pass


@dataclass
class ExtractSummary:
    operation_id: int
    stored: int = 0
    skipped: list[tuple[str, str]] = field(default_factory=list)
    exported_to: Path | None = None


def _safe_relpath(logical_path: str) -> PurePosixPath:
    """Adapters are third-party code: never let a logical path escape the export folder."""
    p = PurePosixPath(logical_path)
    # A POSIX-relative name can still become a drive path or change directories on Windows.
    if (
        p.is_absolute()
        or not p.parts
        or ".." in p.parts
        or "\\" in logical_path
        or ":" in logical_path
    ):
        raise ExtractionError(f"unsafe logical path from adapter: {logical_path!r}")
    return p


def run_extract(
    conn: sqlite3.Connection,
    store_root: Path,
    source_id: int,
    source: Source,
    adapter: Adapter,
    selection: Sequence[str] | None = None,
    export_dir: Path | None = None,
) -> ExtractSummary:
    if not ("extract" in adapter.capabilities and isinstance(adapter, Extractor)):
        raise ExtractionError(f"adapter {adapter.id} does not support extract")
    params = {"selection": list(selection) if selection else None}
    with operation(
        conn,
        "extract",
        source_id=source_id,
        adapter_id=adapter.id,
        adapter_version=adapter.version,
        params=params,
    ) as op_id:
        summary = ExtractSummary(op_id, exported_to=export_dir)
        for item in adapter.extract(source, selection):
            rel = _safe_relpath(item.logical_path)
            if not isinstance(item, Asset):
                summary.skipped.append((item.logical_path, item.reason))
                log.info("skipped %s: %s", item.logical_path, item.reason)
                continue
            sha = put_bytes(conn, store_root, item.data, item.media_type)
            with conn:
                cur = conn.execute(
                    "INSERT INTO artifact (blob_sha256, source_id, kind, logical_path, format,"
                    " metadata_json) VALUES (?, ?, 'extracted', ?, ?, ?)",
                    (
                        sha,
                        source_id,
                        str(rel),
                        item.media_type,
                        json.dumps(item.metadata, sort_keys=True, default=str),
                    ),
                )
                conn.execute(
                    "INSERT INTO edge (operation_id, artifact_id, role) VALUES (?, ?, 'output')",
                    (op_id, cur.lastrowid),
                )
            summary.stored += 1
            if export_dir is not None:
                _export(store_root, sha, export_dir / rel)
    return summary


def _export(store_root: Path, sha: str, dest: Path) -> None:
    """Copy a stored blob to the export folder. The store stays the authoritative copy."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    data = blob_path(store_root, sha).read_bytes()
    if dest.exists() and dest.read_bytes() == data:
        return
    tmp = dest.with_name(dest.name + ".part")
    tmp.write_bytes(data)
    tmp.replace(dest)
