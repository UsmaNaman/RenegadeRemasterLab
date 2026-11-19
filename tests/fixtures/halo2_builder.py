"""Builds tiny SYNTHETIC Halo 2 Xbox cache files (.map) for tests. No real game data, ever."""

from __future__ import annotations

import struct
from collections.abc import Sequence
from functools import reduce
from operator import xor

GROUP_TABLE_ADDR = 0x80061020  # any address works; the tag table is found relative to it
META_ADDR = 0x80100000

DEFAULT_TAGS = [
    ("matg", "globals\\globals", 0x40),
    ("bitm", "ui\\one", 0x20),
    ("bitm", "ui\\two", 0x10),
    ("scnr", "scenarios\\test\\test", 0x30),
    ("sbsp", "scenarios\\test\\bsp", 0),  # BSP tags live outside the meta area
]


def signature(data: bytes) -> int:
    body = data[0x800:] + b"\0" * (-len(data) % 4)
    return reduce(xor, (w for (w,) in struct.iter_unpack("<I", body)), 0)


def meta_address(tags: Sequence[tuple[str, str, int | bytes]], i: int) -> int:
    """Memory address make_map gives tag i (its meta is packed in tag order)."""
    return META_ADDR + sum(m if isinstance(m, int) else len(m) for _, _, m in tags[:i])


def sid(index: int, text: str) -> int:
    """String id make_map gives string_ids[index]: length in the top 8 bits."""
    return len(text) << 24 | index


def tag_datum(i: int) -> int:
    """Datum index make_map gives tag i."""
    return (0xE174 + i) << 16 | i


def scnr_meta(address: int, bsps: Sequence[tuple[int, int, int, int]]) -> bytes:
    """scnr meta at `address`; its BSP block (0x210) lists (file offset, size, base, ltmp datum)."""
    head = bytearray(0x218)
    struct.pack_into("<iI", head, 0x210, len(bsps), address + len(head))
    for off, size, base, ltmp in bsps:
        e = bytearray(0x44)
        struct.pack_into("<3I", e, 0, off, size, base)
        struct.pack_into("<4sI4sI", e, 0x10, b"psbs", 0xFFFFFFFF, b"pmtl", ltmp)
        head += e
    return bytes(head)


def bsp_area(
    base: int,
    bitm: int,
    palettes: Sequence[bytes],
    cluster_info: Sequence[tuple[int, int]],
    instance_info: Sequence[tuple[int, int]] = (),
    ltmp_addr: int | None = None,
) -> bytes:
    """A BSP area loaded at `base`: header, ltmp with one lightmap group naming `bitm`, blocks."""
    ltmp_at, group_at, data_at = 0x10, 0x98, 0x100
    pal = b"".join(palettes)
    ci = b"".join(struct.pack("<hBB", b, p, 0) for b, p in cluster_info)
    ii = b"".join(struct.pack("<hBB", b, p, 0) for b, p in instance_info)
    ltmp = bytearray(0x88)
    struct.pack_into("<iI", ltmp, 0x80, 1, base + group_at)
    group = bytearray(0x68)
    struct.pack_into("<iI", group, 0x08, len(palettes), base + data_at)
    struct.pack_into("<4sI", group, 0x18, b"mtib", bitm)
    struct.pack_into("<iI", group, 0x28, len(cluster_info), base + data_at + len(pal))
    struct.pack_into("<iI", group, 0x48, len(instance_info), base + data_at + len(pal) + len(ci))
    body = bytes(ltmp) + bytes(group).ljust(data_at - group_at, b"\0") + pal + ci + ii
    size = 0x10 + len(body)
    ltmp_ptr = base + ltmp_at if ltmp_addr is None else ltmp_addr
    return struct.pack("<3I4s", size, base + 0x200, ltmp_ptr, b"psbs") + body


def xbe_with_section(xbe: bytes, va: int, data: bytes) -> bytes:
    """`xbe` (from make_xbe) plus one section at `va` holding `data`, appended after the headers."""
    out = bytearray(xbe)
    (base,) = struct.unpack_from("<I", out, 0x104)
    struct.pack_into("<II", out, 0x11C, 1, base + 0x300)
    struct.pack_into("<5I", out, 0x300, 0, va, len(data), len(out), len(data))
    return bytes(out) + data


def bitmap_entry(
    width: int,
    height: int,
    fmt: int,
    pointer: int,
    size: int,
    typ: int = 0,
    flags: int = 0,
    mips: int = 0,
    depth: int = 1,
) -> bytes:
    """One 0x74-byte bitm bitmap entry; only LOD0 is used, LOD1-5 are 0xFFFFFFFF/0."""
    e = bytearray(0x74)
    struct.pack_into(
        "<4sHHBBHHHhhh", e, 0, b"mtib", width, height, depth, 0, typ, fmt, flags, 0, 0, mips
    )
    struct.pack_into("<6I", e, 0x1C, pointer, *[0xFFFFFFFF] * 5)
    struct.pack_into("<6I", e, 0x34, size, 0, 0, 0, 0, 0)
    return bytes(e)


def bitm_meta(address: int, entries: list[bytes]) -> bytes:
    """bitm meta at `address`: 0x4C-byte header whose bitmap block points just past itself."""
    head = bytearray(0x4C)
    struct.pack_into("<iI", head, 0x44, len(entries), address + 0x4C)
    return bytes(head) + b"".join(entries)


def make_map(
    tags: Sequence[tuple[str, str, int | bytes]] = DEFAULT_TAGS,  # meta: size (pattern) or bytes
    internal_name: str = "testmap",
    map_type: int = 1,
    build: str = "00.00.00.00000",
    tail: bytes = b"",
    string_ids: Sequence[str] = (),  # string id i is sid(i, string_ids[i])
) -> bytes:
    groups = sorted({g for g, _, _ in tags})
    index_off = 0x800
    group_table = b"".join(g.encode()[::-1] + b"\xff" * 8 for g in groups)
    tag_table_addr = GROUP_TABLE_ADDR + len(group_table)
    index_size = (0x20 + len(group_table) + 0x10 * len(tags) + 0xFFF) & ~0xFFF

    meta = bytearray()
    entries = bytearray()
    for i, (group, _, m) in enumerate(tags):
        body = bytes((i * 7 + j) & 0xFF for j in range(m)) if isinstance(m, int) else m
        size = len(body)
        address = 0 if size == 0 else META_ADDR + len(meta)
        datum = tag_datum(i)
        entries += struct.pack("<4sIII", group.encode()[::-1], datum, address, size)
        meta += body
    meta_data_size = (len(meta) + 0xFFF) & ~0xFFF

    index = struct.pack("<8I", GROUP_TABLE_ADDR, len(groups), tag_table_addr, 0, 0, 0, len(tags), 0)
    index = (index + group_table + entries).ljust(index_size, b"\0")

    names = bytearray()
    offsets = []
    for _, name, _ in tags:
        offsets.append(len(names))
        names += name.encode() + b"\0"
    name_index = struct.pack(f"<{len(tags)}I", *offsets)
    strings = b"".join(s.encode() + b"\0" for s in string_ids)
    starts = [sum(len(s) + 1 for s in string_ids[:i]) for i in range(len(string_ids))]
    sid_index = struct.pack(f"<{len(starts)}I", *starts)

    body = index + bytes(meta).ljust(meta_data_size, b"\0")
    name_table_off = index_off + len(body)
    name_index_off = name_table_off + len(names)
    sid_index_off = name_index_off + len(name_index)
    sid_table_off = sid_index_off + len(sid_index)
    data = bytearray(0x800) + body + names + name_index + sid_index + strings + tail
    size = len(data)

    data[0:4] = b"daeh"
    data[0x7FC:0x800] = b"toof"
    struct.pack_into(
        "<7I",
        data,
        4,
        8,
        size,
        0,
        index_off,
        index_size,
        meta_data_size,
        index_size + meta_data_size,
    )
    data[0x120 : 0x120 + len(build)] = build.encode()
    struct.pack_into("<I", data, 0x140, map_type)
    struct.pack_into("<4I", data, 0x164, len(starts), len(strings), sid_index_off, sid_table_off)
    data[0x198 : 0x198 + len(internal_name)] = internal_name.encode()
    scenario = f"scenarios\\test\\{internal_name}".encode()
    data[0x1BC : 0x1BC + len(scenario)] = scenario
    struct.pack_into("<4I", data, 0x2C0, len(tags), name_table_off, len(names), name_index_off)
    struct.pack_into("<I", data, 0x2D0, signature(bytes(data)))
    return bytes(data)
