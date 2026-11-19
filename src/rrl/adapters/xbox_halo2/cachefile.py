"""Halo 2 (original Xbox, retail) cache file (.map): header, tag index and signature.

Layout facts and sources: documentation/platforms/halo2_xbox.md.
"""

from __future__ import annotations

import os
import stat
import struct
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import BinaryIO

HEADER_SIZE = 0x800
HEAD_MAGIC = b"daeh"  # "head" stored as a little-endian fourcc
FOOT_MAGIC = b"toof"
VERSION = 8
SIGNATURE_OFFSET = 0x2D0
INDEX_HEADER_SIZE = 0x20
TAG_ENTRY_SIZE = 0x10
MAX_TAGS = 0xFFFF  # the low 16 bits of a datum index are the tag's slot
MAX_NAME_TABLE = 16 * 1024 * 1024
MAX_STRING_IDS = 0x1000000  # the low 24 bits of a string id are its index
MAX_BLOCK = 0x10000
CHUNK = 4 * 1024 * 1024

MAP_TYPES = {
    0: "singleplayer",
    1: "multiplayer",
    2: "mainmenu",
    3: "shared",
    4: "singleplayer_shared",
}


class CacheError(ValueError):
    """Not a parseable Halo 2 Xbox cache file."""


@dataclass(frozen=True)
class CacheHeader:
    version: int
    file_size: int  # as stored in the header
    index_offset: int  # file offset of the tag index header
    index_size: int  # bytes from index_offset to the first tag's meta
    meta_data_size: int
    meta_size: int
    build: str
    map_type: int
    internal_name: str
    scenario_name: str
    name_count: int
    name_table_offset: int
    name_table_size: int
    name_index_offset: int
    signature: int
    string_id_count: int = 0
    string_id_table_size: int = 0
    string_id_index_offset: int = 0
    string_id_table_offset: int = 0

    @property
    def map_type_name(self) -> str:
        return MAP_TYPES.get(self.map_type, f"unknown({self.map_type})")


@dataclass(frozen=True)
class TagEntry:
    group: str  # class fourcc as read by humans, e.g. "bitm"
    datum: int
    address: int  # memory address of the tag's meta
    size: int
    offset: int | None  # file offset of the meta; None when outside the meta area (e.g. sbsp)
    name: str | None


def _size(f: BinaryIO) -> int:
    return f.seek(0, os.SEEK_END)


def _read_at(f: BinaryIO, offset: int, size: int, what: str) -> bytes:
    f.seek(offset)
    data = f.read(size)
    if len(data) != size:
        raise CacheError(f"{what} at {offset:#x} (+{size:#x}) runs past end of file")
    return data


def _cstr(raw: bytes) -> str:
    return raw.split(b"\0", 1)[0].decode("latin-1")


def parse_header(f: BinaryIO) -> CacheHeader:
    raw = _read_at(f, 0, HEADER_SIZE, "header")
    if raw[:4] != HEAD_MAGIC or raw[-4:] != FOOT_MAGIC:
        raise CacheError("missing daeh/toof header magic")
    (version,) = struct.unpack_from("<I", raw, 4)
    if version != VERSION:
        raise CacheError(f"cache version {version}, expected {VERSION} (Halo 2 Xbox)")
    file_size, _, index_offset, index_size, meta_data_size, meta_size = struct.unpack_from(
        "<6I", raw, 8
    )
    (map_type,) = struct.unpack_from("<I", raw, 0x140)
    count, table_off, table_size, idx_off, signature = struct.unpack_from("<5I", raw, 0x2C0)
    sid_count, sid_size, sid_index, sid_table = struct.unpack_from("<4I", raw, 0x164)
    header = CacheHeader(
        version=version,
        file_size=file_size,
        index_offset=index_offset,
        index_size=index_size,
        meta_data_size=meta_data_size,
        meta_size=meta_size,
        build=_cstr(raw[0x120:0x140]),
        map_type=map_type,
        internal_name=_cstr(raw[0x198:0x1BC]),
        scenario_name=_cstr(raw[0x1BC:0x2BC]),
        name_count=count,
        name_table_offset=table_off,
        name_table_size=table_size,
        name_index_offset=idx_off,
        signature=signature,
        string_id_count=sid_count,
        string_id_table_size=sid_size,
        string_id_index_offset=sid_index,
        string_id_table_offset=sid_table,
    )
    actual = _size(f)
    if index_offset < HEADER_SIZE or index_offset + INDEX_HEADER_SIZE > actual:
        raise CacheError(f"tag index offset {index_offset:#x} outside file ({actual:#x} bytes)")
    return header


def compute_signature(f: BinaryIO) -> int:
    """XOR of every little-endian u32 from HEADER_SIZE to EOF (a short tail is zero-padded)."""
    f.seek(HEADER_SIZE)
    acc = 0
    while chunk := f.read(CHUNK):
        chunk += b"\0" * (-len(chunk) % 4)
        # One big int per chunk, folded in halves: ~5x faster than array + reduce.
        n, bits = int.from_bytes(chunk, "little"), len(chunk) * 8
        while bits > 32:
            half = (bits // 64) * 32
            n = (n & ((1 << half) - 1)) ^ (n >> half)
            bits -= half
        acc ^= n
    return acc


def read_tag_index(f: BinaryIO, header: CacheHeader) -> list[TagEntry]:
    base = header.index_offset
    group_addr, _, table_addr, _, _, _, count, _ = struct.unpack(
        "<8I", _read_at(f, base, INDEX_HEADER_SIZE, "tag index header")
    )
    if count > MAX_TAGS:
        raise CacheError(f"absurd tag count {count}")
    # The group table directly follows the index header, which anchors address -> offset.
    table_off = table_addr - (group_addr - INDEX_HEADER_SIZE) + base
    if not base + INDEX_HEADER_SIZE <= table_off <= base + header.index_size:
        raise CacheError(f"tag table address {table_addr:#x} outside the tag index")
    raw = _read_at(f, table_off, count * TAG_ENTRY_SIZE, "tag table")
    rows = [struct.unpack_from("<4sIII", raw, i * TAG_ENTRY_SIZE) for i in range(count)]
    names = _read_names(f, header, count)
    meta_start = base + header.index_size
    first = rows[0][2] if rows else 0
    tags: list[TagEntry] = []
    for i, (fourcc, datum, address, size) in enumerate(rows):
        offset = address - first + meta_start
        end = meta_start + header.meta_data_size
        inside = address != 0 and meta_start <= offset and offset + size <= end
        tags.append(
            TagEntry(
                group=fourcc[::-1].decode("latin-1"),
                datum=datum,
                address=address,
                size=size,
                offset=offset if inside else None,
                name=names[i] if names else None,
            )
        )
    return tags


def _read_names(f: BinaryIO, header: CacheHeader, count: int) -> list[str] | None:
    """Tag names from the file table, or None if absent or not one per tag."""
    if header.name_count != count or not 0 < header.name_table_size <= MAX_NAME_TABLE:
        return None
    index = _read_at(f, header.name_index_offset, count * 4, "tag name index")
    table = _read_at(f, header.name_table_offset, header.name_table_size, "tag name table")
    names = []
    for (start,) in struct.iter_unpack("<I", index):
        end = table.find(b"\0", start)
        if start >= len(table) or end < 0:
            raise CacheError(f"tag name offset {start:#x} outside name table")
        names.append(table[start:end].decode("latin-1"))
    return names


@dataclass
class StringIds:
    """Resolves string ids (names of nodes, regions, markers...). Reads the tables on first use."""

    f: BinaryIO
    header: CacheHeader
    _tables: tuple[bytes, bytes] | None = field(default=None, init=False, repr=False)

    def __call__(self, sid: int) -> str:
        if self._tables is None:
            h = self.header
            if h.string_id_count > MAX_STRING_IDS or h.string_id_table_size > MAX_NAME_TABLE:
                raise CacheError(f"absurd string id table ({h.string_id_count} ids)")
            self._tables = (
                _read_at(self.f, h.string_id_index_offset, 4 * h.string_id_count, "string ids"),
                _read_at(self.f, h.string_id_table_offset, h.string_id_table_size, "strings"),
            )
        index, table = self._tables
        i = sid & 0xFFFFFF
        if 4 * i + 4 > len(index):
            raise CacheError(f"string id {sid:#x} past the {len(index) // 4} ids")
        (start,) = struct.unpack_from("<I", index, 4 * i)
        end = table.find(b"\0", start)
        if start >= len(table) or end < 0 or end - start != sid >> 24:
            raise CacheError(f"string id {sid:#x}: bad offset or length")
        return table[start:end].decode("latin-1")


def block_at(
    f: BinaryIO,
    raw: bytes,
    pos: int,
    element: int,
    tr: Callable[[int, int], int],
    what: str,
) -> bytes:
    """Elements of the tag block whose (count, address) sits at raw[pos:pos + 8].

    `tr(address, size)` turns a memory address into a file offset and raises CacheError when
    the range falls outside the area the caller allows.
    """
    if not 0 <= pos <= len(raw) - 8:
        raise CacheError(f"{what} block header at {pos:#x} outside its parent")
    count, address = struct.unpack_from("<iI", raw, pos)
    if not 0 <= count <= MAX_BLOCK:
        raise CacheError(f"absurd {what} count {count}")
    if count == 0:
        return b""
    size = count * element
    return _read_at(f, tr(address, size), size, what)


def block(
    f: BinaryIO, offset: int, element: int, tr: Callable[[int, int], int], what: str
) -> bytes:
    return block_at(f, _read_at(f, offset, 8, f"{what} block"), 0, element, tr, what)


def resign(path: str | os.PathLike[str]) -> tuple[int, int]:
    """Recompute and write the signature of a map COPY. Returns (old, new).

    Only for edited copies in the workspace: never point this at an original image or file.
    """
    p = Path(path)
    st = p.lstat()
    if not stat.S_ISREG(st.st_mode):
        raise CacheError(f"{p} is not a regular file")
    if not (st.st_mode & 0o222) or not os.access(p, os.W_OK):
        raise CacheError(f"{p} is not writable")
    with p.open("r+b") as f:
        old = parse_header(f).signature  # refuses anything that is not a Halo 2 map
        new = compute_signature(f)
        f.seek(SIGNATURE_OFFSET)
        f.write(struct.pack("<I", new))
    return old, new
