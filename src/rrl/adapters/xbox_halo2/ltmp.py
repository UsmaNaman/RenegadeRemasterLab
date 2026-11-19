"""Halo 2 (original Xbox, retail) lightmap palettes: scnr -> BSP -> ltmp -> P8 palette per bitmap.

Layout facts and sources: documentation/platforms/halo2_xbox.md ("Lightmaps").
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import BinaryIO

from rrl.adapters.xbox_halo2.cachefile import (
    CacheError,
    CacheHeader,
    TagEntry,
    _read_at,
    block,
    block_at,
)

SCNR_BSP_BLOCK = 0x210
BSP_ENTRY_SIZE = 0x44
BSP_SBSP_DATUM = 0x14  # datum half of the sbsp tagref at +0x10
BSP_LTMP_DATUM = 0x1C  # datum half of the ltmp tagref at +0x18
BSP_HEADER_SIZE = 0x10
BSP_MAGIC = b"psbs"  # "sbsp" as a little-endian fourcc
LTMP_GROUPS = 0x80
GROUP_SIZE = 0x68
GROUP_PALETTES = 0x08
GROUP_BITM_DATUM = 0x1C  # datum half of the bitm tagref at +0x18
GROUP_CLUSTER_INFO = 0x28
GROUP_INSTANCE_INFO = 0x48
PALETTE_SIZE = 0x400  # 256 x u32 A8R8G8B8, stored B, G, R, A
RENDER_INFO_SIZE = 4  # i16 bitmap index, u8 palette index, u8 pad
NONE = 0xFFFFFFFF
MAX_BSPS = 64

# bitm datum -> bitmap index -> 1024-byte palette, or the reason it has none
Palettes = dict[int, dict[int, bytes | str]]


@dataclass(frozen=True)
class BspEntry:
    """One scnr Structure BSPs entry: where the BSP area lies and the tags it holds."""

    scenario: str
    index: int
    offset: int  # file offset of the BSP area
    size: int
    base: int  # memory address the area loads at
    sbsp: int  # datum
    ltmp: int  # datum, NONE = no lightmap

    def tr(self, address: int, size: int) -> int:
        # BSP pointers are memory addresses relative to the BSP's own load address.
        offset = self.offset + address - self.base
        if not self.offset <= offset <= offset + size <= self.offset + self.size:
            raise CacheError(
                f"address {address:#x} (+{size:#x}) outside BSP at {self.offset:#x} "
                f"(+{self.size:#x})"
            )
        return offset

    def addresses(self, f: BinaryIO) -> tuple[int, int]:
        """(sbsp, ltmp) memory addresses from the area header."""
        _, sbsp, ltmp, magic = struct.unpack(
            "<3I4s",
            _read_at(f, self.tr(self.base, BSP_HEADER_SIZE), BSP_HEADER_SIZE, "BSP header"),
        )
        if magic != BSP_MAGIC:
            raise CacheError(f"BSP at {self.offset:#x} lacks {BSP_MAGIC!r} magic")
        return sbsp, ltmp


@dataclass(frozen=True)
class LightmapGroup:
    bitm: int  # datum of the lightmap bitm, NONE = none
    palettes: bytes  # N x PALETTE_SIZE
    clusters: list[tuple[int, int]]  # (bitmap index, palette index) per cluster; -1 = none
    instances: list[tuple[int, int]]  # the same per instance


def bsp_entries(f: BinaryIO, header: CacheHeader, tags: list[TagEntry]) -> list[BspEntry]:
    """Every structure BSP named by the map's scnr tags. Raises CacheError on a bad scnr."""
    out: list[BspEntry] = []
    meta_start = header.index_offset + header.index_size
    for scnr in (t for t in tags if t.group == "scnr"):
        name = scnr.name or f"scnr_{scnr.datum:08x}"
        if scnr.offset is None or scnr.size < SCNR_BSP_BLOCK + 8:
            raise CacheError(f"{name}: scnr meta missing or too small ({scnr.size:#x} bytes)")
        count, address = struct.unpack(
            "<iI", _read_at(f, scnr.offset + SCNR_BSP_BLOCK, 8, f"{name} BSP block")
        )
        if not 0 <= count <= MAX_BSPS:
            raise CacheError(f"{name}: absurd BSP count {count}")
        if count == 0:
            continue
        offset = address - tags[0].address + meta_start
        if not meta_start <= offset <= meta_start + header.meta_data_size - count * BSP_ENTRY_SIZE:
            raise CacheError(f"{name}: BSP block address {address:#x} outside tag meta")
        raw = _read_at(f, offset, count * BSP_ENTRY_SIZE, f"{name} BSP entries")
        for i in range(count):
            at = i * BSP_ENTRY_SIZE
            bsp_off, bsp_size, bsp_base = struct.unpack_from("<3I", raw, at)
            (sbsp,) = struct.unpack_from("<I", raw, at + BSP_SBSP_DATUM)
            (ltmp,) = struct.unpack_from("<I", raw, at + BSP_LTMP_DATUM)
            out.append(BspEntry(name, i, bsp_off, bsp_size, bsp_base, sbsp, ltmp))
    return out


def lightmap_groups(f: BinaryIO, bsp: BspEntry) -> list[LightmapGroup]:
    """The ltmp lightmap groups of a BSP with a lightmap. Raises CacheError when malformed."""
    tr = bsp.tr
    ltmp = tr(bsp.addresses(f)[1], LTMP_GROUPS + 8)
    groups = block(f, ltmp + LTMP_GROUPS, GROUP_SIZE, tr, "lightmap groups")
    out = []
    for g in range(0, len(groups), GROUP_SIZE):
        (bitm,) = struct.unpack_from("<I", groups, g + GROUP_BITM_DATUM)
        if bitm == NONE:  # mainmenu: a group with no lightmap bitmap
            out.append(LightmapGroup(bitm, b"", [], []))
            continue

        def infos(pos: int, what: str, g: int = g) -> list[tuple[int, int]]:
            data = block_at(f, groups, g + pos, RENDER_INFO_SIZE, tr, what)
            return [(b, p) for b, p, _ in struct.iter_unpack("<hBB", data)]

        out.append(
            LightmapGroup(
                bitm,
                block_at(f, groups, g + GROUP_PALETTES, PALETTE_SIZE, tr, "section palettes"),
                infos(GROUP_CLUSTER_INFO, "cluster render info"),
                infos(GROUP_INSTANCE_INFO, "instance render info"),
            )
        )
    return out


def read_lightmap_palettes(
    f: BinaryIO, header: CacheHeader, tags: list[TagEntry], errors: list[str] | None = None
) -> Palettes:
    """Palette of every lightmap bitmap the map's BSPs name. Raises CacheError on a bad scnr.

    A malformed BSP loses only its own lightmaps: its error is appended to `errors` (if given).

    A bitmap whose render info is contradictory or points past the palette list maps to a reason
    string instead of a palette; bitmaps no render info names are simply absent.
    """
    out: Palettes = {}
    for bsp in bsp_entries(f, header, tags):
        if bsp.ltmp == NONE:  # shared maps carry a BSP without a lightmap
            continue
        try:
            groups = lightmap_groups(f, bsp)
        except CacheError as exc:
            if errors is not None:
                errors.append(f"{bsp.scenario} BSP {bsp.index}: {exc}")
            continue
        for group in groups:
            if group.bitm == NONE:
                continue
            n = len(group.palettes) // PALETTE_SIZE
            found = out.setdefault(group.bitm, {})
            for bitmap, index in group.clusters + group.instances:
                if bitmap < 0:  # (-1, 255): no lightmap
                    continue
                new: bytes | str = (
                    group.palettes[index * PALETTE_SIZE : (index + 1) * PALETTE_SIZE]
                    if index < n
                    else f"palette index {index} but only {n} section palettes"
                )
                old = found.setdefault(bitmap, new)
                if old != new:
                    found[bitmap] = f"render info gives bitmap {bitmap} conflicting palettes"
    return out
