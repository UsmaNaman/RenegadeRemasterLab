"""Builds tiny SYNTHETIC Original Xbox disc images for tests. No real game data, ever."""

from __future__ import annotations

import struct
from pathlib import Path
from typing import Any

SECTOR = 2048
MAGIC = b"MICROSOFT*XBOX*MEDIA"
REDUMP_OFFSET = 0x18300000

Tree = dict[str, Any]  # name -> bytes (file) or Tree (directory)


def make_xbe(
    title: str = "RRL Test Disc",
    title_id: int = 0x524C0001,
    region: int = 0x1 | 0x4,
    media: int = 0x2,
    version: int = 3,
) -> bytes:
    base, cert_off = 0x10000, 0x180
    xbe = bytearray(0x400)
    xbe[0:4] = b"XBEH"
    struct.pack_into("<I", xbe, 0x104, base)
    struct.pack_into("<I", xbe, 0x118, base + cert_off)
    struct.pack_into("<II", xbe, cert_off, 0x1D0, 0)
    struct.pack_into("<I", xbe, cert_off + 0x08, title_id)
    xbe[cert_off + 0x0C : cert_off + 0x5C] = title.encode("utf-16-le").ljust(0x50, b"\0")
    struct.pack_into("<II", xbe, cert_off + 0x9C, media, region)
    struct.pack_into("<I", xbe, cert_off + 0xAC, version)
    return bytes(xbe)


def dir_table(entries: list[tuple[str, int, int, int]]) -> bytes:
    """A directory table: (name, sector, size, attributes) arranged as a balanced binary tree."""
    entries = sorted(entries, key=lambda e: e[0].upper())
    order: list[int] = []  # pre-order, so the tree root lands at offset 0

    def preorder(lo: int, hi: int) -> int | None:
        if lo >= hi:
            return None
        mid = (lo + hi) // 2
        order.append(mid)
        left[mid], right[mid] = preorder(lo, mid), preorder(mid + 1, hi)
        return mid

    left: dict[int, int | None] = {}
    right: dict[int, int | None] = {}
    preorder(0, len(entries))

    offsets: dict[int, int] = {}
    pos = 0
    for i in order:
        length = (14 + len(entries[i][0]) + 3) & ~3
        if pos // SECTOR != (pos + length - 1) // SECTOR:  # entries never cross a sector
            pos = (pos // SECTOR + 1) * SECTOR
        offsets[i] = pos
        pos += length

    table = bytearray(b"\xff" * (-(-pos // SECTOR) * SECTOR))
    for i in order:
        name, sector, size, attrs = entries[i]
        lft, rgt = left[i], right[i]
        node = struct.pack(
            "<HHIIBB",
            0 if lft is None else offsets[lft] // 4,
            0 if rgt is None else offsets[rgt] // 4,
            sector,
            size,
            attrs,
            len(name),
        ) + name.encode("latin-1")
        table[offsets[i] : offsets[i] + len(node)] = node
    return bytes(table)


def volume_descriptor(root_sector: int, root_size: int) -> bytes:
    vd = bytearray(SECTOR)
    vd[0:20] = MAGIC
    struct.pack_into("<II", vd, 0x14, root_sector, root_size)
    vd[0x7EC:0x800] = MAGIC
    return bytes(vd)


def build_partition(tree: Tree) -> bytes:
    """An XDVDFS partition (i.e. an XISO) containing `tree`."""
    image = bytearray(34 * SECTOR)

    def alloc(data: bytes) -> int:
        sector = len(image) // SECTOR
        image.extend(data.ljust(-(-len(data) // SECTOR) * SECTOR, b"\0"))
        return sector

    def write_dir(node: Tree) -> tuple[int, int]:
        rows = []
        for name, child in node.items():
            if isinstance(child, dict):
                sector, size = write_dir(child)
                rows.append((name, sector, size, 0x10))
            else:
                rows.append((name, alloc(child) if child else 0, len(child), 0x20))
        if not rows:
            return 0, 0  # empty directory
        table = dir_table(rows)
        return alloc(table), len(table)

    root = write_dir(tree)
    image[32 * SECTOR : 33 * SECTOR] = volume_descriptor(*root)
    return bytes(image)


def sample_tree() -> Tree:
    return {
        "default.xbe": make_xbe(),
        "Readme.TXT": b"hello from a synthetic disc\n",
        "maps": {
            "alpha.map": bytes(range(256)) * 20,  # spans more than one sector
            "beta.map": b"B" * 100,
            "sub": {"deep.bin": b"deep"},
        },
        "empty": {},
        "zero.dat": b"",
    }


def write_image(path: Path, data: bytes, partition_offset: int = 0) -> Path:
    """Write a partition at an offset; the gap before it stays sparse on disk."""
    with path.open("wb") as f:
        f.seek(partition_offset)
        f.write(data)
    return path
