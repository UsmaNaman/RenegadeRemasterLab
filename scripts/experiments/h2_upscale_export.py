"""Experiment: Real-ESRGAN 4x over the packable textures of an `rrl extract --export` folder.

Picks the export PNGs whose artifacts carry xemu_hashes, runs realesrgan-ncnn-vulkan once over a
flat staging folder, and writes the results to <out_dir> with the export's relative paths,
ready for `rrl pack build`. Resumable: finished outputs are skipped, and a texture shared by
several maps is upscaled once. Results wider or taller than MAX_SIDE are scaled to 2x instead,
to stay inside xemu's 64 MiB staging buffer. Reads only RRL's own export and database.

Usage: uv run python scripts/experiments/h2_upscale_export.py <source_id> <export_dir> <out_dir>
       [path_prefix]
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from PIL import Image

from rrl.core.config import load_config

UPSCALER = Path("~/Applications/realesrgan-ncnn-vulkan/realesrgan-ncnn-vulkan").expanduser()
MAX_SIDE = 4096
CHUNK = 200


def main(argv: list[str]) -> int:
    source_id, export, out = int(argv[0]), Path(argv[1]).expanduser(), Path(argv[2]).expanduser()
    prefix = argv[3] if len(argv) > 3 else ""
    conn = sqlite3.connect(load_config().db_path)
    rows = conn.execute(
        "SELECT DISTINCT logical_path, metadata_json FROM artifact WHERE source_id = ?"
        " AND metadata_json LIKE '%\"xemu_hashes\"%' AND logical_path LIKE ? || '%'",
        (source_id, prefix),
    ).fetchall()
    # Shared textures appear once per map: upscale one copy per hash set. Skip finished ones so
    # an interrupted run resumes.
    todo: dict[str, dict[str, Any]] = {}
    seen: set[str] = set()
    for p, m in rows:
        meta = json.loads(m)
        key = meta["xemu_hashes"][0]
        if key not in seen and not (out / p).exists():
            todo[p] = meta
        seen.add(key)
    items = list(todo.items())
    halved = 0
    for start in range(0, len(items), CHUNK):
        chunk = items[start : start + CHUNK]
        with tempfile.TemporaryDirectory(dir=out.parent) as tmp:
            staged, done = Path(tmp, "in"), Path(tmp, "out")
            staged.mkdir()
            done.mkdir()
            for i, (p, _) in enumerate(chunk):
                (staged / f"{i:05d}.png").hardlink_to(export / p)
            subprocess.run(
                [str(UPSCALER), "-i", str(staged), "-o", str(done), "-n", "realesrgan-x4plus"]
                + ["-m", str(UPSCALER.parent / "models")],
                check=True,
                capture_output=True,
            )
            for i, (p, meta) in enumerate(chunk):
                img = Image.open(done / f"{i:05d}.png")
                w, h = meta["width"], meta["height"]
                assert img.size == (w * 4, h * 4), (p, img.size)
                if max(img.size) > MAX_SIDE:
                    img = img.resize((w * 2, h * 2), Image.Resampling.LANCZOS)
                    halved += 1
                dest = out / p
                dest.parent.mkdir(parents=True, exist_ok=True)
                img.save(dest)
        print(f"{start + len(chunk)}/{len(items)} upscaled", flush=True)
    print(f"done: {len(items)} upscaled into {out} ({halved} kept at 2x), {len(seen)} unique")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
