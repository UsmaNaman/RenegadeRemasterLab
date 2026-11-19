"""Experiment (ADR-0009 milestone 1): do RRL's offline texture hashes match xemu's?

For every bitmap of one Halo 2 map, hashes the bytes xemu would hash (rrl.emulators.xemu_textures)
for each stored LOD copy and every plausible level count, then looks them up in an xemu-vulkan
texture dump (index.tsv from XEMU_TEXTURE_DUMP_DIR). Read-only on the image.

Usage: uv run python scripts/experiments/h2_xemu_hashes.py <image.iso> <dump_dir> [map]
"""

from __future__ import annotations

import csv
import sys
from collections import Counter
from pathlib import Path
from typing import BinaryIO

from rrl.adapters.xbox_halo2 import LINEAR_PITCH, _map_entries
from rrl.adapters.xbox_halo2.bitmaps import read_bitmaps, read_raw
from rrl.adapters.xbox_halo2.cachefile import CacheError, parse_header, read_tag_index
from rrl.containers.xdvdfs import XdvdfsImage, find_partition
from rrl.emulators.xemu_textures import content_hash, texture_length
from rrl.processors.textures import FORMATS, bytes_needed


def main(argv: list[str]) -> int:
    iso, dump = Path(argv[0]).expanduser(), Path(argv[1]).expanduser()
    map_name = argv[2] if len(argv) > 2 else "mainmenu"
    with (dump / "index.tsv").open() as fh:
        rows = list(csv.DictReader(fh, delimiter="\t"))
    dumped = {int(r["raw_hash"], 16): r for r in rows}

    # hash -> (tag, bitmap index, lod, levels)
    ours: dict[int, tuple[str, int, int, int]] = {}
    status: Counter[str] = Counter()
    with iso.open("rb") as f:
        partition = find_partition(f)
        if partition is None:
            raise SystemExit("no XDVDFS partition")
        img = XdvdfsImage(f, partition)
        paths = {e.path.split("/")[-1].lower(): e.path for e in _map_entries(img)}
        opened: dict[str, BinaryIO] = {}

        def opener(name: str) -> BinaryIO:
            if name not in opened:
                opened[name] = img.open(paths[name])
            return opened[name]

        m = opener(f"{map_name}.map")
        header = parse_header(m)
        for bt in read_bitmaps(m, header, read_tag_index(m, header)):
            for b in bt.bitmaps:
                if b.format not in FORMATS or b.type not in ("2d", "cube"):
                    status["unsupported format/type"] += 1
                    continue
                cube = b.type == "cube"
                linear = b.linear and not b.format.startswith("dxt")
                found = False
                for lod, loc in enumerate(b.lods):
                    if loc is None:
                        continue
                    try:
                        raw = read_raw(loc, m, opener)
                    except CacheError:
                        continue
                    w, h = max(1, b.width >> lod), max(1, b.height >> lod)
                    pitch = None
                    if linear:
                        row = bytes_needed(b.format, w, 1)
                        pitch = -(-row // LINEAR_PITCH) * LINEAR_PITCH
                    for levels in range(1, b.mips - lod + 2) if not linear else (1,):
                        n = texture_length(b.format, w, h, levels, cube=cube, pitch=pitch)
                        if n > len(raw):
                            continue  # span runs past the stored chain: not reproducible
                        key = content_hash(raw[:n])
                        ours.setdefault(key, (bt.name, b.index, lod, levels))
                        found |= key in dumped
                status["matched" if found else "not seen in dump"] += 1

    matched = {k: v for k, v in ours.items() if k in dumped}
    print(
        f"{map_name}: {sum(status.values())} bitmaps; "
        + ", ".join(f"{k} {v}" for k, v in status.items())
    )
    print(f"dump: {len(dumped)} distinct textures, {len(matched)} matched by an RRL hash")
    by_lod = Counter(lod for _, _, lod, _ in matched.values())
    print(f"matches by LOD copy: {dict(sorted(by_lod.items()))}")
    full_chain = sum(
        1
        for (_, _, lod, levels), k in zip(matched.values(), matched, strict=True)
        if lod == 0 and int(dumped[k]["levels"]) == levels
    )
    print(f"LOD0 matches where xemu's level count equals RRL's: {full_chain}")
    unmatched = Counter(
        (r["color_format"], r["width"], r["height"], r["levels"], r["cubemap"], r["linear"])
        for k, r in dumped.items()
        if k not in matched
    )
    print("unmatched dump shapes (fmt, w, h, levels, cube, linear): count")
    for shape, n in unmatched.most_common(15):
        print(f"  {shape}: {n}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
