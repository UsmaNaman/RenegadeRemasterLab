"""Halo 2 (original Xbox, retail) bitmap (`bitm`) tags: per-bitmap fields and raw pixel location.

Layout facts and sources: documentation/platforms/halo2_xbox.md ("Bitmaps").
"""

from __future__ import annotations

import os
import struct
from collections.abc import Callable
from dataclasses import dataclass
from typing import BinaryIO

from rrl.adapters.xbox_halo2.cachefile import CacheError, CacheHeader, TagEntry, _read_at

BITM_BLOCK_OFFSET = 0x44  # tag block (count, address) of bitmap entries in the bitm meta
BITMAP_ENTRY_SIZE = 0x74
LOD_OFFSETS = 0x1C  # 6 x u32 raw pointers, LOD0 (full resolution) first
LOD_SIZES = 0x34  # 6 x u32 byte sizes
UNUSED_POINTER = 0xFFFFFFFF
MAX_BITMAPS = 4096
MAX_RAW_SIZE = 64 * 1024 * 1024

FORMATS = {
    0: "a8",
    1: "y8",
    2: "ay8",
    3: "a8y8",
    6: "r5g6b5",
    8: "a1r5g5b5",
    9: "a4r4g4b4",
    10: "x8r8g8b8",
    11: "a8r8g8b8",
    14: "dxt1",
    15: "dxt3",
    16: "dxt5",
    17: "p8_bump",
    18: "p8",
    19: "argbfp32",
    20: "rgbfp32",
    21: "rgbfp16",
    22: "v8u8",
    23: "g8b8",
}
TYPES = {0: "2d", 1: "3d", 2: "cube"}
# Top 2 bits of a raw pointer pick the file; 0 is the map itself.
RAW_FILES = {1: "mainmenu.map", 2: "shared.map", 3: "single_player_shared.map"}

FLAG_SWIZZLED = 1 << 3
FLAG_LINEAR = 1 << 4


@dataclass(frozen=True)
class RawLocation:
    file: int  # 0 local map, else a RAW_FILES key
    offset: int
    size: int

    @property
    def file_name(self) -> str | None:
        return RAW_FILES.get(self.file)


@dataclass(frozen=True)
class Bitmap:
    index: int
    width: int
    height: int
    depth: int
    type: str  # TYPES value or "unknown(n)"
    format: str  # FORMATS value or "unknown(n)"
    flags: int
    mips: int  # mip levels below the full-resolution one
    lod0: RawLocation | None  # full-resolution mip chain; None if not stored
    # LOD0-LOD5; LOD n is a separate copy of the chain starting at mip n (None = unused)
    lods: tuple[RawLocation | None, ...] = ()

    @property
    def swizzled(self) -> bool:
        return bool(self.flags & FLAG_SWIZZLED)

    @property
    def linear(self) -> bool:
        return bool(self.flags & FLAG_LINEAR)


@dataclass(frozen=True)
class BitmapTag:
    name: str
    datum: int
    bitmaps: list[Bitmap]


def read_bitmaps(f: BinaryIO, header: CacheHeader, tags: list[TagEntry]) -> list[BitmapTag]:
    """Every bitm tag in the map. Raises CacheError on the first malformed one."""
    return [read_bitmap_tag(f, header, tags, t) for t in tags if t.group == "bitm"]


def read_bitmap_tag(
    f: BinaryIO, header: CacheHeader, tags: list[TagEntry], tag: TagEntry
) -> BitmapTag:
    name = tag.name or f"bitm_{tag.datum:08x}"
    if tag.offset is None or tag.size < BITM_BLOCK_OFFSET + 8:
        raise CacheError(f"{name}: bitm meta missing or too small ({tag.size:#x} bytes)")
    count, address = struct.unpack(
        "<iI", _read_at(f, tag.offset + BITM_BLOCK_OFFSET, 8, f"{name} bitmap block")
    )
    if not 0 <= count <= MAX_BITMAPS:
        raise CacheError(f"{name}: absurd bitmap count {count}")
    if count == 0:
        return BitmapTag(name, tag.datum, [])
    # Tag blocks use the same address space as tag meta.
    meta_start = header.index_offset + header.index_size
    offset = address - tags[0].address + meta_start
    if not meta_start <= offset <= meta_start + header.meta_data_size - count * BITMAP_ENTRY_SIZE:
        raise CacheError(f"{name}: bitmap block address {address:#x} outside tag meta")
    raw = _read_at(f, offset, count * BITMAP_ENTRY_SIZE, f"{name} bitmap entries")
    return BitmapTag(name, tag.datum, [_bitmap(raw, i) for i in range(count)])


def _bitmap(raw: bytes, i: int) -> Bitmap:
    base = i * BITMAP_ENTRY_SIZE
    w, h, depth, _, typ, fmt, flags, _, _, mips = struct.unpack_from("<HHBBHHHhhh", raw, base + 4)
    pointers = struct.unpack_from("<6I", raw, base + LOD_OFFSETS)
    sizes = struct.unpack_from("<6I", raw, base + LOD_SIZES)
    lods = tuple(
        None if p == UNUSED_POINTER or s == 0 else RawLocation(p >> 30, p & 0x3FFFFFFF, s)
        for p, s in zip(pointers, sizes, strict=True)
    )
    return Bitmap(
        index=i,
        width=w,
        height=h,
        depth=depth,
        type=TYPES.get(typ, f"unknown({typ})"),
        format=FORMATS.get(fmt, f"unknown({fmt})"),
        flags=flags,
        mips=mips,
        lod0=lods[0],
        lods=lods,
    )


def read_raw(
    location: RawLocation, map_file: BinaryIO, shared_opener: Callable[[str], BinaryIO]
) -> bytes:
    """Read raw bytes from the map itself or the shared map the pointer selects.

    shared_opener maps a file name (e.g. "shared.map") to an open handle; callers cache them.
    """
    if location.size > MAX_RAW_SIZE:
        raise CacheError(f"absurd raw data size {location.size:#x}")
    name = location.file_name
    f = map_file if name is None else shared_opener(name)
    end = f.seek(0, os.SEEK_END)
    if location.offset + location.size > end:
        raise CacheError(
            f"raw data {location.offset:#x}+{location.size:#x} past end of "
            f"{name or 'map'} ({end:#x} bytes)"
        )
    return _read_at(f, location.offset, location.size, "raw data")
