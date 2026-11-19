"""Experiment (ADR-0009 milestone 2): build a visible test texture pack for xemu-vulkan-dev.

For every 2D, swizzled (non-linear) DXT1/3/5 or A8R8G8B8 bitmap of one Halo 2 map: decode LOD0,
upscale it 4x and swap red and blue so a replacement is obvious on screen, then write one pack
file per plausible xemu hash (the game sometimes programs fewer levels than are stored, see
milestone 1). Read-only on the image. The pack holds derived game pixels: keep it in the
workspace, never in the repo.

Usage: uv run python scripts/experiments/h2_xemu_pack.py <image.iso> <pack_dir> [map]
Then:  XEMU_TEXTURE_PACK_DIR=<pack_dir> ~/Applications/xemu-vulkan-dev.app/Contents/MacOS/xemu
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import BinaryIO

from PIL import Image

from rrl.adapters.xbox_halo2 import _map_entries
from rrl.adapters.xbox_halo2.bitmaps import read_bitmaps, read_raw
from rrl.adapters.xbox_halo2.cachefile import CacheError, parse_header, read_tag_index
from rrl.containers.xdvdfs import XdvdfsImage, find_partition
from rrl.emulators.xemu_textures import (
    PACK_FORMATS,
    candidate_hashes,
    write_pack_texture,
)
from rrl.processors.textures import decode

SCALE = 4


def main(argv: list[str]) -> int:
    iso, pack = Path(argv[0]).expanduser(), Path(argv[1]).expanduser()
    map_name = argv[2] if len(argv) > 2 else "mainmenu"
    pack.mkdir(parents=True, exist_ok=True)
    index: list[str] = []
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
                loc = b.lods[0]
                if b.format not in PACK_FORMATS or b.type != "2d" or b.linear or loc is None:
                    continue
                try:
                    raw = read_raw(loc, m, opener)
                except CacheError:
                    continue
                dxt = b.format.startswith("dxt")
                image = decode(raw, b.format, b.width, b.height, morton=b.swizzled and not dxt)
                r, g, bl, a = image.convert("RGBA").split()
                big = Image.merge("RGBA", (bl, g, r, a)).resize(
                    (b.width * SCALE, b.height * SCALE), Image.Resampling.LANCZOS
                )
                keys = candidate_hashes(raw, b.format, b.width, b.height, b.mips + 1)
                if not keys:
                    continue
                write_pack_texture(pack, keys, big, b.width, b.height)
                index += [f"{k:016x}\t{bt.name}\t{b.index}\t{n}" for n, k in enumerate(keys, 1)]
    (pack / "index.tsv").write_text("hash\ttag\tbitmap\tlevels\n" + "\n".join(index) + "\n")
    print(f"{map_name}: wrote {len(index)} pack files to {pack}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
