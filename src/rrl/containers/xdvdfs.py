"""Read-only XDVDFS (Original Xbox disc filesystem) reader.

Format notes and sources: documentation/platforms/xbox_original.md.
"""

from __future__ import annotations

import io
import os
import struct
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from rrl.api import Entry

SECTOR = 2048
MAGIC = b"MICROSOFT*XBOX*MEDIA"
VOLUME_DESCRIPTOR = 32 * SECTOR
# Partition offset -> image layout name. Redump keeps the video partition in front of the game.
LAYOUTS = {0: "xiso", 0x18300000: "redump"}
ATTR_DIRECTORY = 0x10
MAX_DEPTH = 64
MAX_TABLE = 16 * 1024 * 1024  # far above any real disc; stops a forged size eating memory
_NODE = struct.Struct("<HHIIBB")  # left, right (4-byte units), sector, size, attributes, name len


class XdvdfsError(ValueError):
    """The image is not a valid XDVDFS filesystem."""


@dataclass(frozen=True)
class _Node:
    name: str
    sector: int
    size: int
    is_dir: bool


def _file_size(f: BinaryIO) -> int:
    return f.seek(0, os.SEEK_END)


def _read_at(f: BinaryIO, offset: int, size: int) -> bytes:
    f.seek(offset)
    data = f.read(size)
    if len(data) != size:
        raise XdvdfsError(f"short read at {offset:#x}: wanted {size}, got {len(data)}")
    return data


def _check_descriptor(f: BinaryIO, partition: int, file_size: int) -> tuple[int, int]:
    """Return (root sector, root size) of the volume descriptor, or raise XdvdfsError."""
    if partition + VOLUME_DESCRIPTOR + SECTOR > file_size:
        raise XdvdfsError("image too small for an XDVDFS volume descriptor")
    vd = _read_at(f, partition + VOLUME_DESCRIPTOR, SECTOR)
    if vd[:20] != MAGIC or vd[0x7EC:0x800] != MAGIC:
        raise XdvdfsError(f"no XDVDFS volume descriptor at partition {partition:#x}")
    root_sector, root_size = struct.unpack_from("<II", vd, 0x14)
    return root_sector, root_size


def find_partition(f: BinaryIO) -> int | None:
    """Offset of the XDVDFS game partition, or None if the image has none we recognise."""
    size = _file_size(f)
    for offset in LAYOUTS:
        try:
            _check_descriptor(f, offset, size)
        except XdvdfsError:
            continue
        return offset
    return None


class XdvdfsImage:
    """A read-only Container over an XDVDFS partition. Never writes to the image."""

    def __init__(self, image: BinaryIO | str | os.PathLike[str], partition_offset: int = 0):
        if isinstance(image, (str, os.PathLike)):
            self._f: BinaryIO = Path(image).open("rb")  # noqa: SIM115 - closed by close()
            self._owned = True
        else:
            self._f = image
            self._owned = False
        self.partition_offset = partition_offset
        try:
            self._size = _file_size(self._f)
            self._root = _check_descriptor(self._f, partition_offset, self._size)
        except BaseException:
            self.close()
            raise

    def close(self) -> None:
        if self._owned:
            self._f.close()

    def __enter__(self) -> XdvdfsImage:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _extent(self, sector: int, size: int) -> int:
        """Absolute offset of an extent, after checking it lies inside the image."""
        start = self.partition_offset + sector * SECTOR
        if start + size > self._size:
            raise XdvdfsError(f"extent at sector {sector} ({size} bytes) runs past end of image")
        return start

    def _children(self, sector: int, size: int) -> list[_Node]:
        """Parse one directory table (a binary tree of entries), in tree order."""
        if size == 0:
            return []  # empty directory
        if size > MAX_TABLE:
            raise XdvdfsError(f"directory table at sector {sector} is implausibly large: {size}")
        table = _read_at(self._f, self._extent(sector, size), size)
        if table[:2] == b"\xff\xff":
            return []  # padding where the first entry would be: empty directory
        nodes: list[_Node] = []
        seen: set[int] = set()
        stack = [0]
        while stack:
            offset = stack.pop()
            if offset in seen:
                raise XdvdfsError(f"directory table at sector {sector} has a loop")
            seen.add(offset)
            if offset + _NODE.size > size:
                raise XdvdfsError(f"directory entry at {offset:#x} runs past its table")
            left, right, child_sector, child_size, attrs, name_len = _NODE.unpack_from(
                table, offset
            )
            if left == 0xFFFF or right == 0xFFFF:
                raise XdvdfsError(f"directory tree points into padding at {offset:#x}")
            name_end = offset + _NODE.size + name_len
            if name_len == 0 or name_end > size:
                raise XdvdfsError(f"bad name length at {offset:#x}")
            name = table[offset + _NODE.size : name_end].decode("latin-1")
            if name in (".", "..") or "/" in name or "\\" in name or "\0" in name:
                raise XdvdfsError(f"unsafe file name {name!r}")
            nodes.append(_Node(name, child_sector, child_size, bool(attrs & ATTR_DIRECTORY)))
            for sub in (right, left):
                if sub:
                    stack.append(sub * 4)
        return nodes

    def entries(self) -> Iterator[Entry]:
        yield from self._walk(*self._root, "", 0, set())

    def _walk(
        self, sector: int, size: int, prefix: str, depth: int, seen: set[int]
    ) -> Iterator[Entry]:
        if depth > MAX_DEPTH:
            raise XdvdfsError(f"directory tree deeper than {MAX_DEPTH} at {prefix!r}")
        # Real discs never share a table, so any revisit is a cycle (or a fork bomb).
        if size and sector in seen:
            raise XdvdfsError(f"directory cycle at {prefix!r}")
        seen.add(sector)
        for node in sorted(self._children(sector, size), key=lambda n: n.name.lower()):
            path = prefix + node.name
            if node.is_dir:
                yield Entry(path, 0, True)
                yield from self._walk(node.sector, node.size, path + "/", depth + 1, seen)
            else:
                self._extent(node.sector, node.size)
                yield Entry(path, node.size, False)

    def open(self, path: str) -> BinaryIO:
        """Open a file (case-insensitive path) as a read-only window into the image."""
        start, size = self.locate(path)
        return io.BufferedReader(_Window(self._f, start, size))

    def locate(self, path: str) -> tuple[int, int]:
        """(absolute byte offset in the image, size) of a file. For in-place patching of copies."""
        parts = [p for p in path.replace("\\", "/").split("/") if p]
        sector, size = self._root
        node: _Node | None = None
        for i, part in enumerate(parts):
            wanted = part.lower()
            node = next((n for n in self._children(sector, size) if n.name.lower() == wanted), None)
            if node is None or (i < len(parts) - 1 and not node.is_dir):
                raise FileNotFoundError(path)
            sector, size = node.sector, node.size
        if node is None:
            raise FileNotFoundError(path)
        if node.is_dir:
            raise IsADirectoryError(path)
        return self._extent(node.sector, node.size), node.size


class _Window(io.RawIOBase):
    """A read-only, seekable view of [start, start + size) of a shared file."""

    def __init__(self, f: BinaryIO, start: int, size: int):
        self._f, self._start, self._len, self._pos = f, start, size, 0

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return True

    def tell(self) -> int:
        return self._pos

    def seek(self, offset: int, whence: int = os.SEEK_SET) -> int:
        base = {os.SEEK_SET: 0, os.SEEK_CUR: self._pos, os.SEEK_END: self._len}[whence]
        if base + offset < 0:
            raise ValueError("negative seek position")
        self._pos = base + offset
        return self._pos

    def readinto(self, buffer: memoryview) -> int:  # type: ignore[override]
        n = min(len(buffer), self._len - self._pos)
        if n <= 0:
            return 0
        # Seek every time: other windows may share the underlying file handle.
        self._f.seek(self._start + self._pos)
        data = self._f.read(n)
        buffer[: len(data)] = data
        self._pos += len(data)
        return len(data)
