"""Experiment: a visible texture change in a COPY of Halo 2 (Xbox), booted in xemu.

Clones the owner's XISO (APFS copy-on-write; the original is only ever read), then rewrites the
colour endpoints of every DXT block of the selected mainmenu.map bitmaps to magenta, in place
and same-size, and re-signs the map. Alpha data and the DXT1 block mode (which decides 1-bit
transparency) are kept, so shapes and cut-outs survive and only the colour changes.

Usage: uv run python scripts/experiments/h2_texture_swap.py <original.iso> <out.iso> [substr...]
  substr  only bitmaps whose tag name contains one of these (case-insensitive); default all.
"""

from __future__ import annotations

import struct
import subprocess
import sys
from pathlib import Path

import numpy as np

from rrl.adapters.xbox_halo2.bitmaps import read_bitmaps
from rrl.adapters.xbox_halo2.cachefile import (
    SIGNATURE_OFFSET,
    compute_signature,
    parse_header,
    read_tag_index,
)
from rrl.containers.xdvdfs import XdvdfsImage, find_partition
from rrl.processors.textures import decode

MAP = "maps/mainmenu.map"
MAGENTA, MAGENTA_DIM = 0xF81F, 0xF81E  # RGB565; c0 > c1 selects DXT 4-colour mode
COLOUR_AT = {"dxt1": (8, 0), "dxt3": (16, 8), "dxt5": (16, 8)}  # block size, colour offset


def recolour(raw: bytearray, fmt: str) -> None:
    size, at = COLOUR_AT[fmt]
    for base in range(at, len(raw) - size + at + 1, size):
        c0, c1 = struct.unpack_from("<HH", raw, base)
        # DXT1 with c0 <= c1 is 3-colour + transparent: keep that ordering so alpha survives.
        pair = (MAGENTA_DIM, MAGENTA) if fmt == "dxt1" and c0 <= c1 else (MAGENTA, MAGENTA_DIM)
        struct.pack_into("<HH", raw, base, *pair)


def main(argv: list[str]) -> int:
    original, out = Path(argv[0]).expanduser(), Path(argv[1]).expanduser()
    wanted = [s.lower() for s in argv[2:]]
    if out.exists():
        raise SystemExit(f"{out} exists; refusing to overwrite")
    subprocess.run(["cp", "-c", str(original), str(out)], check=True)
    out.chmod(0o644)
    changed = []
    with out.open("r+b") as f:
        partition = find_partition(f)
        if partition is None:
            raise SystemExit("no XDVDFS partition in copy")
        img = XdvdfsImage(f, partition)
        map_start, map_size = img.locate(MAP)
        with img.open(MAP) as m:
            header = parse_header(m)
            targets = [
                (bt.name, b)
                for bt in read_bitmaps(m, header, read_tag_index(m, header))
                for b in bt.bitmaps
                if b.lod0 is not None
                and b.lod0.file_name is None  # raw stored in mainmenu.map itself
                and b.format in COLOUR_AT
                and b.type in ("2d", "cube")
                and (not wanted or any(w in bt.name.lower() for w in wanted))
            ]
        for name, b in targets:
            assert b.lod0 is not None
            if b.lod0.offset + b.lod0.size > map_size:
                raise SystemExit(f"{name}: raw data past end of map")
            f.seek(map_start + b.lod0.offset)
            raw = bytearray(f.read(b.lod0.size))
            recolour(raw, b.format)
            f.seek(map_start + b.lod0.offset)
            f.write(raw)
            changed.append((name, b))
        f.flush()
        with img.open(MAP) as m:
            new = compute_signature(m)
        f.seek(map_start + SIGNATURE_OFFSET)
        f.write(struct.pack("<I", new))
        f.flush()
        with img.open(MAP) as m:
            stored, computed = parse_header(m).signature, compute_signature(m)
        # Self-check: the first changed 2D texture now decodes to (near) magenta.
        name, b = next((n, b) for n, b in changed if b.type == "2d")
        assert b.lod0 is not None
        f.seek(map_start + b.lod0.offset)
        rgb = decode(f.read(b.lod0.size), b.format, b.width, b.height).convert("RGB")
        r, g, bl = np.asarray(rgb).reshape(-1, 3).mean(axis=0)
        assert r > 200 and g < 20 and bl > 200, (name, r, g, bl)
    print(f"recoloured {len(changed)} bitmaps in {MAP}")
    print(f"signature stored={stored:08x} computed={computed:08x} valid={stored == computed}")
    print(f"self-check {name}: mean RGB {r:.0f},{g:.0f},{bl:.0f}")
    return 0 if stored == computed else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
