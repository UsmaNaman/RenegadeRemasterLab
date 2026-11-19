"""Batch texture enhancement with a locally supplied realesrgan-ncnn-vulkan executable."""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import tempfile
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from itertools import batched, groupby
from pathlib import Path
from typing import Any

from PIL import Image

from rrl.core.extraction import _safe_relpath
from rrl.core.hashing import sha256_file
from rrl.processors.textures import to_png
from rrl.store.blobs import blob_path, put_bytes


@dataclass(frozen=True)
class Texture:
    """A unique source PNG and the scale wanted for its mirrored outputs."""

    path: Path
    scale: int


def upscale_batch(
    backend: Path, model: str, textures: Sequence[Texture], scratch: Path
) -> Iterator[bytes]:
    """Run one native 4x inference batch, yielding validated PNGs in input order."""
    with tempfile.TemporaryDirectory(dir=scratch) as tmp:
        inputs, outputs = Path(tmp, "rgb"), Path(tmp, "upscaled")
        inputs.mkdir()
        outputs.mkdir()
        for i, texture in enumerate(textures):
            with Image.open(texture.path, formats=["PNG"]) as original:
                original.convert("RGB").save(inputs / f"{i}.png")

        proc = subprocess.run(
            [
                str(backend),
                "-i",
                str(inputs),
                "-o",
                str(outputs),
                "-s",
                "4",
                "-n",
                model,
                "-m",
                str(backend.parent / "models"),
                "-f",
                "png",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        if proc.returncode:
            detail = proc.stderr.decode(errors="replace").strip()
            raise ValueError(f"{backend.name} exited with status {proc.returncode}: {detail}")

        for i, texture in enumerate(textures):
            with (
                Image.open(texture.path, formats=["PNG"]) as original,
                Image.open(outputs / f"{i}.png", formats=["PNG"]) as enhanced,
            ):
                w, h = original.size
                if enhanced.size != (4 * w, 4 * h):
                    raise ValueError(
                        f"{backend.name} produced {enhanced.size}, expected {4 * w}x{4 * h}"
                    )
                rgb = enhanced.convert("RGB")
                size = (w * texture.scale, h * texture.scale)
                if rgb.size != size:
                    rgb = rgb.resize(size, Image.Resampling.LANCZOS)
                if original.has_transparency_data:
                    alpha = original.convert("RGBA").getchannel("A")
                    rgb.putalpha(alpha.resize(size, Image.Resampling.LANCZOS))
                yield to_png(rgb)


def _inside(root: Path, logical_path: str) -> Path:
    path = root / _safe_relpath(logical_path)
    if not path.resolve().is_relative_to(root):
        raise ValueError(f"{logical_path} resolves outside {root}")
    return path


def _skip_reason(meta: dict[str, Any], adapter_id: str | None, packable_only: bool) -> str | None:
    fmt = meta.get("format")
    if meta.get("type") == "3d" or "slice" in meta:
        return "3D texture slice"
    if fmt in ("p8_bump", "v8u8", "g8b8"):
        return f"{fmt} bump/normal map"
    if fmt == "p8" and adapter_id == "xbox.halo2":
        return "P8 lightmap"
    if packable_only and not meta.get("xemu_hashes"):
        return "no xemu pack hashes"
    return None


def _exported_textures(
    conn: sqlite3.Connection,
    source_id: int,
    export: Path,
    only: tuple[str, ...],
    packable_only: bool,
) -> dict[str, list[tuple[int, str]]]:
    rows = conn.execute(
        "SELECT a.id, a.logical_path, a.blob_sha256, a.metadata_json, o.adapter_id FROM artifact a"
        " LEFT JOIN edge e ON e.artifact_id = a.id AND e.role = 'output'"
        " LEFT JOIN operation o ON o.id = e.operation_id"
        " WHERE a.source_id = ? AND a.kind = 'extracted' AND format = 'image/png'"
        " ORDER BY a.logical_path, a.id DESC",
        (source_id,),
    )
    groups: dict[str, list[tuple[int, str]]] = {}
    for name, history in groupby(rows, lambda row: str(row["logical_path"])):
        if only and not name.startswith(only):
            continue
        path = _inside(export, name)
        if not path.is_file():
            print(f"skipped {name}: absent from export")
            continue
        # A source can have several extractions. Match the bytes in this particular export.
        sha = sha256_file(path)
        row = next((r for r in history if r["blob_sha256"] == sha), None)
        if row is None:
            raise ValueError(f"{name} does not match an extracted blob for source {source_id}")
        if reason := _skip_reason(
            json.loads(row["metadata_json"]), row["adapter_id"], packable_only
        ):
            print(f"skipped {name}: {reason}")
            continue
        groups.setdefault(sha, []).append((row["id"], name))
    return groups


def _publish(blob: Path, dest: Path, sha: str) -> bool:
    """Publish a complete, independently editable copy, including on concurrent resumes."""
    if not dest.exists():
        dest.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=dest.parent, delete=False) as tmp:
            stage = Path(tmp.name)
        try:
            shutil.copyfile(blob, stage)
            try:
                os.link(stage, dest)
                return True
            except FileExistsError:
                pass
        finally:
            stage.unlink()
    if sha256_file(dest) != sha:
        raise ValueError(f"existing output differs: {dest}; choose a separate --out directory")
    return False


def _record(
    conn: sqlite3.Connection,
    source_id: int,
    op_id: int,
    source_artifact: int,
    name: str,
    sha: str,
    scale: int,
    model: str,
) -> None:
    meta = json.dumps(
        {"source_artifact_id": source_artifact, "scale": scale, "model": model}, sort_keys=True
    )
    with conn:
        # A concurrent resume may already have registered this exact derivation.
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT id FROM artifact WHERE source_id = ? AND logical_path = ? AND kind = 'enhanced'"
            " AND metadata_json = ? AND blob_sha256 = ?",
            (source_id, name, meta, sha),
        ).fetchone()
        if row is None:
            artifact_id = conn.execute(
                "INSERT INTO artifact (source_id, logical_path, kind, format, metadata_json,"
                " blob_sha256) VALUES (?, ?, 'enhanced', 'image/png', ?, ?)",
                (source_id, name, meta, sha),
            ).lastrowid
        else:
            artifact_id = row["id"]
        conn.executemany(
            "INSERT INTO edge (operation_id, artifact_id, role) VALUES (?, ?, ?)",
            [(op_id, source_artifact, "input"), (op_id, artifact_id, "output")],
        )


def upscale_export(
    conn: sqlite3.Connection,
    store: Path,
    source_id: int,
    op_id: int,
    *,
    export: Path,
    out: Path,
    backend: Path,
    model: str,
    scale: int,
    max_side: int,
    only: tuple[str, ...],
    packable_only: bool,
) -> None:
    if not export.is_dir():
        raise ValueError(f"export directory does not exist: {export}")
    if out == export:
        raise ValueError("--export and --out must be different directories")
    if max_side <= 0:
        raise ValueError("--max-side must be positive")
    groups = _exported_textures(conn, source_id, export, only, packable_only)
    out.mkdir(parents=True, exist_ok=True)
    # Stored results also cover aliases added later and outputs lost after an interruption.
    cached = {
        (original, factor): result
        for original, factor, result in conn.execute(
            "SELECT a.blob_sha256, json_extract(e.metadata_json, '$.scale'), e.blob_sha256"
            " FROM artifact e JOIN artifact a ON a.id = json_extract(e.metadata_json,"
            " '$.source_artifact_id') WHERE e.kind = 'enhanced' AND e.source_id = ?"
            " AND json_extract(e.metadata_json, '$.model') = ? ORDER BY e.id",
            (source_id, model),
        )
    }
    written = resumed = processed = 0

    def mirror(sha: str, factor: int, result: str) -> None:
        nonlocal written, resumed
        for artifact_id, name in groups[sha]:
            dest = _inside(out, name)
            # Commit lineage before publication so a retry can recover a missing mirror.
            _record(conn, source_id, op_id, artifact_id, name, result, factor, model)
            if _publish(blob_path(store, result), dest, result):
                written += 1
            else:
                resumed += 1

    pending: list[Texture] = []
    for sha in groups:
        original = blob_path(store, sha)
        with Image.open(original, formats=["PNG"]) as png:
            factor = 2 if max(png.size) * scale > max_side else scale
        if result := cached.get((sha, factor)):
            mirror(sha, factor, result)
        else:
            pending.append(Texture(original, factor))

    for batch in batched(pending, 200):
        for texture, data in zip(batch, upscale_batch(backend, model, batch, out), strict=True):
            result = put_bytes(conn, store, data, "image/png")
            mirror(texture.path.name, texture.scale, result)
            processed += 1
    print(f"upscaled {processed} unique textures; wrote {written} PNGs, resumed {resumed} in {out}")
