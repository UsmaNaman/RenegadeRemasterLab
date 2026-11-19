"""Read the primary ISO9660 tree in cooked images and raw CD data tracks."""

from __future__ import annotations

import io
import re
import struct
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from rrl.api import Entry

BLOCK = 2048
SYNC = b"\x00" + b"\xff" * 10 + b"\x00"
MODES = {"MODE1/2048": (2048, 0), "MODE1/2352": (2352, 16), "MODE2/2352": (2352, 24)}


class IsoError(ValueError):
    pass


def _frames(value: str) -> int:
    match = re.fullmatch(r"(\d{2}):([0-5]\d):(\d\d)", value)
    if not match or int(match[3]) >= 75:
        raise IsoError("invalid cue index time")
    return (int(match[1]) * 60 + int(match[2])) * 75 + int(match[3])


def resolve_cue(path: Path) -> tuple[Path, str, int, int | None]:
    """Resolve track 01 only. Return its file, mode and frame interval."""
    with path.open("rb") as f:
        data = f.read(65537)
    if len(data) > 65536:
        raise IsoError("oversized cue sheet")
    name = mode = ""
    track = start = end = None
    for line in data.decode("utf-8-sig").splitlines():
        words = line.strip().split(maxsplit=1)
        if not words:
            continue
        key = words[0].upper()
        arg = words[1] if len(words) > 1 else ""
        if key == "FILE":
            if name:
                break
            m = re.fullmatch(r'(?:"([^"]+)"|(\S+))\s+BINARY', arg, re.I)
            if not m:
                raise IsoError("expected binary FILE in cue")
            name = m[1] or m[2]
        elif key == "TRACK":
            m = re.fullmatch(r"(\d\d)\s+(\S+)", arg.upper())
            if not m or not name:
                raise IsoError("invalid cue TRACK")
            if track is None:
                mode = m[2]
                if m[1] != "01" or mode not in MODES:
                    raise IsoError("unsupported first cue track")
            track = m[1]
        elif key == "INDEX":
            parts = arg.split()
            if len(parts) != 2 or track is None:
                raise IsoError("invalid cue INDEX")
            frame = _frames(parts[1])
            if track == "01" and parts[0] == "01":
                start = frame
            elif track != "01" and parts[0] in {"00", "01"}:
                end = frame
                break
    if start is None or (track != "01" and end is None):
        raise IsoError("missing cue track index")
    relative = Path(name.replace("\\", "/"))
    if relative.is_absolute() or ".." in relative.parts or relative.suffix.lower() != ".bin":
        raise IsoError("expected local relative .bin track")
    target = (path.parent / relative).resolve()
    if not target.is_relative_to(path.parent.resolve()) or not target.is_file():
        raise IsoError("missing or external cue track")
    return target, mode, start, end


def _both(data: bytes, pos: int, width: int) -> int:
    fmt = {2: "H", 4: "I"}[width]
    value = struct.unpack_from("<" + fmt, data, pos)[0]
    if value != struct.unpack_from(">" + fmt, data, pos + width)[0]:
        raise IsoError("inconsistent both-endian field")
    return int(value)


@dataclass(frozen=True)
class _Record:
    name: str
    sector: int
    size: int
    directory: bool


class Iso9660Image:
    def __init__(self, image: BinaryIO | str | Path):
        self._owned = isinstance(image, (str, Path))
        mode = None
        start = 0
        end = None
        if isinstance(image, (str, Path)):
            path = Path(image)
            if path.suffix.lower() == ".cue":
                path, mode, start, end = resolve_cue(path)
            self._f: BinaryIO = path.open("rb")
        else:
            self._f = image
        try:
            length = self._f.seek(0, 2)
            for candidate, (stride, header) in MODES.items():
                self.base = start * stride
                self._f.seek(self.base + 16 * stride + header + 1)
                if (mode is None or mode == candidate) and self._f.read(5) == b"CD001":
                    self.mode, self.stride, self.header = candidate, stride, header
                    break
            else:
                raise IsoError("missing ISO9660 descriptor")
            available = length // self.stride
            if end is not None and not start < end <= available:
                raise IsoError("invalid cue track end")
            self.blocks = (end if end is not None else available) - start
            root = None
            for sector in range(16, min(self.blocks, 16 + 256)):
                vd = self._sector(sector)
                if vd[1:7] != b"CD001\x01":
                    raise IsoError("invalid volume descriptor")
                if vd[0] == 1 and root is None:
                    blocks = _both(vd, 80, 4)
                    if not sector < blocks <= self.blocks or _both(vd, 128, 2) != BLOCK:
                        raise IsoError("invalid volume size or block size")
                    self.blocks = blocks
                    root = self._record(vd[156 : 156 + vd[156]])
                    if not root.directory or root.name != "\0":
                        raise IsoError("invalid root directory")
                if vd[0] == 255:
                    break
            else:
                raise IsoError("missing descriptor terminator")
            if root is None:
                raise IsoError("missing primary volume descriptor")
            self._files: dict[str, _Record] = {}
            self._entries: list[Entry] = []
            self._directory_bytes = 0
            self._walk(root, "", set(), 0)
        except BaseException:
            self.close()
            raise

    def _sector(self, n: int) -> bytes:
        if not 0 <= n < self.blocks:
            raise IsoError("sector outside volume")
        self._f.seek(self.base + n * self.stride)
        raw = self._f.read(self.stride)
        if len(raw) != self.stride:
            raise IsoError("truncated sector")
        if self.header and (raw[:12] != SYNC or raw[15] != (1 if self.header == 16 else 2)):
            raise IsoError("invalid raw sector header")
        if self.header == 24 and (raw[16:20] != raw[20:24] or raw[18] & 32):
            raise IsoError("expected XA Form 1 sector")
        return raw[self.header : self.header + BLOCK]

    def _record(self, rec: bytes) -> _Record:
        if len(rec) < 34 or rec[0] != len(rec) or not 0 < rec[32] <= len(rec) - 33:
            raise IsoError("invalid directory record length")
        if rec[25] & 128 or any(rec[26:28]) or _both(rec, 28, 2) != 1:
            raise IsoError("unsupported multi-extent, interleaved or multi-volume file")
        sector = _both(rec, 2, 4) + rec[1]
        size = _both(rec, 10, 4)
        if sector * BLOCK + size > self.blocks * BLOCK:
            raise IsoError("extent past end of volume")
        name = rec[33 : 33 + rec[32]].decode("ascii")
        return _Record(name, sector, size, bool(rec[25] & 2))

    def _walk(self, directory: _Record, prefix: str, seen: set[int], depth: int) -> None:
        if directory.sector in seen:
            raise IsoError("directory cycle")
        if depth > 32 or directory.size > 8 * 1024 * 1024 or len(seen) >= 8192:
            raise IsoError("directory exceeds traversal limit")
        self._directory_bytes += directory.size
        if self._directory_bytes > 64 * 1024 * 1024:
            raise IsoError("combined directory size exceeds traversal limit")
        seen.add(directory.sector)
        for off in range(0, directory.size, BLOCK):
            block = self._sector(directory.sector + off // BLOCK)[: directory.size - off]
            pos = 0
            while pos < len(block) and (length := block[pos]):
                node = self._record(block[pos : pos + length])
                pos += length
                if node.name in {"\0", "\1"}:
                    continue
                name = re.sub(r";\d+$", "", node.name)
                if name in {"", ".", ".."} or re.search(r"[/\\\x00-\x1f]", name):
                    raise IsoError("invalid file identifier")
                path = prefix + name
                if path.upper() in self._files or len(self._files) >= 200000:
                    raise IsoError("duplicate path or entry limit exceeded")
                self._files[path.upper()] = node
                self._entries.append(Entry(path, node.size, node.directory))
                if node.directory:
                    self._walk(node, path + "/", seen, depth + 1)

    def entries(self) -> Iterator[Entry]:
        yield from self._entries

    def _file(self, path: str) -> _Record:
        key = re.sub(r";\d+$", "", path.replace("\\", "/").strip("/")).upper()
        node = self._files.get(key)
        if node is None:
            raise FileNotFoundError(path)
        if node.directory:
            raise IsADirectoryError(path)
        return node

    def locate(self, path: str) -> tuple[int, int]:
        """Physical start and physical span, including intervening raw-sector framing."""
        node = self._file(path)
        gaps = max(0, (node.size - 1) // BLOCK)
        return (
            self.base + node.sector * self.stride + self.header,
            node.size + gaps * (self.stride - BLOCK),
        )

    def open(self, path: str) -> BinaryIO:
        return io.BufferedReader(_File(self, self._file(path)))

    def close(self) -> None:
        if self._owned:
            self._f.close()

    def __enter__(self) -> Iso9660Image:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


class _File(io.RawIOBase):
    def __init__(self, image: Iso9660Image, node: _Record):
        self.image, self.node, self.pos = image, node, 0

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return True

    def tell(self) -> int:
        return self.pos

    def seek(self, offset: int, whence: int = 0) -> int:
        bases = {0: 0, 1: self.pos, 2: self.node.size}
        if whence not in bases or bases[whence] + offset < 0:
            raise ValueError("invalid seek")
        self.pos = bases[whence] + offset
        return self.pos

    def readinto(self, buffer: memoryview) -> int:  # type: ignore[override]
        sector, offset = divmod(self.pos, BLOCK)
        n = min(len(buffer), self.node.size - self.pos, BLOCK - offset)
        if n <= 0:
            return 0
        data = self.image._sector(self.node.sector + sector)
        buffer[:n] = data[offset : offset + n]
        self.pos += n
        return n
