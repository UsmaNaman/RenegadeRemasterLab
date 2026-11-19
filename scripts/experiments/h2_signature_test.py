"""Experiment: does Halo 2 (Xbox) reject a modified map, and does fixing the signature fix that?

Builds two APFS clones of the owner's Halo 2 XISO inside the workspace (the original is never
opened for writing):
  control   one padding byte in mainmenu.map changed, signature left stale
  resigned  same byte changed, signature recomputed
The byte is in the zero padding between the tag-name table and its index, which the game never
reads, so any behaviour difference can only come from the signature check.

Usage: uv run python scripts/experiments/h2_signature_test.py <original.iso> <out_dir>
"""

from __future__ import annotations

import struct
import subprocess
import sys
from pathlib import Path

from rrl.adapters.xbox_halo2.cachefile import SIGNATURE_OFFSET, compute_signature, parse_header
from rrl.containers.xdvdfs import XdvdfsImage, find_partition

MAP = "maps/mainmenu.map"


def padding_offset(img: XdvdfsImage) -> int:
    """Offset within the map of a byte in the padding after the tag-name table."""
    with img.open(MAP) as f:
        h = parse_header(f)
        start, end = h.name_table_offset + h.name_table_size, h.name_index_offset
        if end - start < 16:
            raise SystemExit("no padding gap after the name table; aborting")
        f.seek(start)
        if f.read(end - start).strip(b"\0"):
            raise SystemExit("gap after name table is not zero padding; aborting")
    return (start + end) // 2 & ~3


def make_variant(original: Path, out: Path, resign: bool) -> dict[str, object]:
    if out.exists():
        raise SystemExit(f"{out} exists; refusing to overwrite")
    subprocess.run(["cp", "-c", str(original), str(out)], check=True)  # APFS clone, copy-on-write
    out.chmod(0o644)
    with out.open("r+b") as f:
        partition = find_partition(f)
        if partition is None:
            raise SystemExit("no XDVDFS partition in copy")
        img = XdvdfsImage(f, partition)
        map_start, _ = img.locate(MAP)
        off = padding_offset(img)
        f.seek(map_start + off)
        assert f.read(1) == b"\0"
        f.seek(map_start + off)
        f.write(b"\x01")
        if resign:
            with img.open(MAP) as m:
                new = compute_signature(m)
            f.seek(map_start + SIGNATURE_OFFSET)
            f.write(struct.pack("<I", new))
        f.flush()
        with img.open(MAP) as m:
            stored, computed = parse_header(m).signature, compute_signature(m)
    return {
        "image": str(out),
        "patched_map_offset": hex(off),
        "stored": f"{stored:08x}",
        "computed": f"{computed:08x}",
        "valid": stored == computed,
    }


def main(argv: list[str]) -> int:
    original, out_dir = Path(argv[0]).expanduser(), Path(argv[1]).expanduser()
    out_dir.mkdir(parents=True, exist_ok=True)
    for name, resign in (("control", False), ("resigned", True)):
        result = make_variant(original, out_dir / f"halo2-{name}.iso", resign)
        print(name, result)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
