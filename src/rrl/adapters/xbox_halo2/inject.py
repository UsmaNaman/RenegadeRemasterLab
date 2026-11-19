"""Write a replacement texture into a COPY of a Halo 2 disc image, in place and same size.

The bitmap's LOD0 chain and its LOD1-5 copies are re-encoded from one image, then every map file
that was touched is re-signed (the signature is the only integrity gate; see halo2_xbox.md).
Callers own the copy: this module never opens anything itself and refuses no path, so it must
only ever be handed a writable clone, never the registered original.
"""

from __future__ import annotations

import bisect
import hashlib
import struct
from collections.abc import Callable
from dataclasses import dataclass
from functools import cache
from typing import BinaryIO

from PIL import Image, ImageChops, ImageStat

from rrl.adapters.xbox_halo2 import LINEAR_PITCH
from rrl.adapters.xbox_halo2.bitmaps import Bitmap, read_bitmap_tag
from rrl.adapters.xbox_halo2.cachefile import (
    SIGNATURE_OFFSET,
    CacheError,
    TagEntry,
    compute_signature,
    parse_header,
    read_tag_index,
)
from rrl.containers.xdvdfs import XdvdfsImage, find_partition
from rrl.processors.textures import ENCODABLE_FORMATS, bytes_needed, decode, encode_mip_chain

RAW_ALIGN = 128


@dataclass(frozen=True)
class Injection:
    tag: str
    index: int
    format: str
    size: tuple[int, int]
    writes: list[tuple[str, int, int]]  # (map file, offset in map, bytes) per LOD copy
    signatures: dict[str, tuple[int, int]]  # map file -> (old, new) signature


type Write = tuple[str, int, int]


class NotInjectable(CacheError):
    """An unsupported replacement that batch mode can skip before writing any bytes."""


def _matches_image(got: Image.Image, image: Image.Image, fmt: str) -> bool:
    """Compare only representable channels, allowing the format's quantisation error."""
    if fmt == "dxt1":
        visible = image.getchannel("A").point(lambda a: 255 if a >= 128 else 0)
        if got.getchannel("A").tobytes() != visible.tobytes():
            return False
        if visible.getbbox() is None:
            return True
        diff = ImageChops.difference(got.convert("RGB"), image.convert("RGB"))
        return sum(ImageStat.Stat(diff, visible).mean) / 4 <= 16  # existing DXT error budget
    if fmt in ("dxt3", "dxt5"):
        return sum(ImageStat.Stat(ImageChops.difference(got, image)).mean) / 4 <= 16

    if fmt == "a8":
        return got.getchannel("L").getextrema() == (255, 255) and (
            got.getchannel("A").tobytes() == image.getchannel("A").tobytes()
        )
    if fmt == "ay8":
        y = image.convert("L").tobytes()
        return got.getchannel("L").tobytes() == got.getchannel("A").tobytes() == y
    if fmt in ("y8", "a8y8"):
        return got.tobytes() == image.convert(got.mode).tobytes()
    if fmt in ("x8r8g8b8", "r5g6b5"):
        image = image.convert("RGB").convert("RGBA")
    elif fmt == "a1r5g5b5":
        image = image.copy()
        image.putalpha(image.getchannel("A").point(lambda a: 255 if a >= 128 else 0))
    tolerances = {
        "r5g6b5": (4, 2, 4, 0),
        "a1r5g5b5": (4, 4, 4, 0),
        "a4r4g4b4": (8, 8, 8, 8),
    }.get(fmt, (0, 0, 0, 0))
    errors = ImageStat.Stat(ImageChops.difference(got, image)).extrema
    return all(high <= limit for (_, high), limit in zip(errors, tolerances, strict=True))


class DiscInjector:
    """Accumulate writes to one staged disc. Call finish() before publishing it."""

    def __init__(self, disc: BinaryIO) -> None:
        partition = find_partition(disc)
        if partition is None:
            raise CacheError("no XDVDFS partition in the disc copy")
        self.disc = disc
        self.xiso = XdvdfsImage(disc, partition)
        self.paths = {
            e.path.split("/")[-1].lower(): e.path
            for e in self.xiso.entries()
            if not e.is_dir and e.path.lower().endswith(".map")
        }
        self.tags: dict[str, Callable[[str], list[Bitmap]]] = {}
        self.expected: dict[Write, bytes] = {}
        self.spans: dict[str, list[tuple[int, int]]] = {}  # file -> sorted (offset, size)
        self.targets: dict[tuple[str, str, int], Bitmap] = {}

    def _tag_lookup(self, own: str) -> Callable[[str], list[Bitmap]]:
        if own not in self.paths:
            raise CacheError(f"no {own} on the disc")
        path, xiso = self.paths[own], self.xiso
        with xiso.open(path) as m:
            header = parse_header(m)
            tags = read_tag_index(m, header)
        by_name: dict[str, TagEntry] = {}
        for entry in tags:
            if entry.group == "bitm":
                name = entry.name or f"bitm_{entry.datum:08x}"
                by_name.setdefault(name.lower(), entry)

        @cache
        def lookup(tag: str) -> list[Bitmap]:
            if tag not in by_name:
                raise CacheError(f"no bitm tag {tag}")
            with xiso.open(path) as m:
                return read_bitmap_tag(m, header, tags, by_name[tag]).bitmaps

        return lookup

    def _bitmap(self, own: str, tag: str, index: int) -> Bitmap:
        if own not in self.tags:
            self.tags[own] = self._tag_lookup(own)
        bitmaps = self.tags[own](tag.lower())
        if not 0 <= index < len(bitmaps):
            raise CacheError(f"{tag} has {len(bitmaps)} bitmaps, no index {index}")
        return bitmaps[index]

    def write(
        self, map_name: str, tag: str, index: int, image: Image.Image, *, resize: bool = False
    ) -> tuple[Bitmap, list[Write]]:
        own = f"{map_name.lower()}.map"
        bitmap = self._bitmap(own, tag, index)
        if bitmap.format not in ENCODABLE_FORMATS or bitmap.type != "2d":
            raise NotInjectable(
                f"{tag}[{index}]: {bitmap.type} {bitmap.format} is not injectable yet"
            )
        if bitmap.lod0 is None:
            raise CacheError(f"{tag}[{index}]: no stored LOD0 pixel data to replace")
        size = (bitmap.width, bitmap.height)
        if image.size != size:
            if not resize:
                raise NotInjectable(f"image is {image.size}, the bitmap is {size[0]}x{size[1]}")
            image = image.convert("RGBA").resize(size, Image.Resampling.LANCZOS)

        image = image.convert("RGBA")
        compressed = bitmap.format.startswith("dxt")
        alignment = LINEAR_PITCH if bitmap.linear and not compressed else 1
        morton = bitmap.swizzled and not compressed

        planned: list[tuple[int, Write, bytes, bytes]] = []  # (disc offset, write, chain, digest)
        for n, loc in enumerate(bitmap.lods):
            if loc is None:
                continue
            file = loc.file_name or own
            if file not in self.paths:
                raise CacheError(f"{tag}[{index}] LOD{n}: {file} not on the disc")
            start, length = self.xiso.locate(self.paths[file])
            if loc.offset + loc.size > length:
                raise CacheError(f"{tag}[{index}] LOD{n}: raw data past the end of {file}")
            level = (
                image
                if n == 0
                else image.resize(
                    (max(1, bitmap.width >> n), max(1, bitmap.height >> n)),
                    Image.Resampling.LANCZOS,
                )
            )
            levels = bitmap.mips + 1 - n
            w, h = level.size
            sizes = [
                bytes_needed(bitmap.format, max(1, w >> k), max(1, h >> k), row_alignment=alignment)
                for k in range(levels)
            ]
            # Some stored chains omit trailing tiny levels [V: 2048x128 DXT5 stores 11 of 12].
            while levels > 1 and sum(sizes[:levels]) > loc.size:
                levels -= 1
            chain = encode_mip_chain(
                level, bitmap.format, levels, morton=morton, row_alignment=alignment
            )
            # Stored chains are padded to RAW_ALIGN. Leave that padding untouched.
            if not loc.size - RAW_ALIGN < len(chain) <= loc.size:
                raise CacheError(
                    f"{tag}[{index}] LOD{n}: {levels} levels are {len(chain)} bytes, "
                    f"stored {loc.size}"
                )
            if n == 0:
                surface = chain[: sizes[0]]
                if alignment > 1:
                    row, pitch = bytes_needed(bitmap.format, w, 1), sizes[0] // h
                    surface = b"".join(surface[y * pitch : y * pitch + row] for y in range(h))
                got = decode(surface, bitmap.format, *size, morton=morton)
                if not _matches_image(got, image, bitmap.format):
                    raise CacheError(f"{tag}[{index}]: texture does not decode back to the image")
            write = (file, loc.offset, len(chain))
            digest = hashlib.sha256(chain).digest()
            if self._conflicts(write, digest):
                # Halo 2 shares identical raw data between bitmaps; the first edit wins.
                raise NotInjectable(
                    f"{tag}[{index}] LOD{n}: shares {file}+{loc.offset:#x} with another edit"
                )
            planned.append((start + loc.offset, write, chain, digest))
        for at, write, chain, digest in planned:  # nothing is written unless every LOD fits
            self.disc.seek(at)
            self.disc.write(chain)
            if write not in self.expected:
                self.expected[write] = digest
                bisect.insort(self.spans.setdefault(write[0], []), (write[1], write[2]))
        self.targets[own, tag.lower(), index] = bitmap
        return bitmap, [w for _, w, _, _ in planned]

    def _conflicts(self, write: Write, digest: bytes) -> bool:
        """True if `write` overlaps an earlier edit, unless it is the identical range and bytes."""
        if write in self.expected:
            return self.expected[write] != digest
        file, offset, size = write
        spans = self.spans.get(file, [])
        i = bisect.bisect_left(spans, (offset, size))
        before = spans[i - 1] if i else None
        after = spans[i] if i < len(spans) else None
        return bool(
            (before and before[0] + before[1] > offset) or (after and after[0] < offset + size)
        )

    def finish(self) -> dict[str, tuple[int, int]]:
        """Re-sign each physical map once, then verify signatures and final LOD contents."""
        self.disc.flush()
        signatures = {}
        for file in sorted({file for file, _, _ in self.expected}):
            start, _ = self.xiso.locate(self.paths[file])
            with self.xiso.open(self.paths[file]) as m:
                old, new = parse_header(m).signature, compute_signature(m)
            self.disc.seek(start + SIGNATURE_OFFSET)
            self.disc.write(struct.pack("<I", new))
            signatures[file] = (old, new)
        self.disc.flush()
        for file, (_, new) in signatures.items():
            with self.xiso.open(self.paths[file]) as m:
                if parse_header(m).signature != new or compute_signature(m) != new:
                    raise CacheError(f"{file}: signature does not hold after the write")
        # The final tag index must still point to the layout whose encoded pixels we checked.
        self.tags.clear()
        for (own, tag, index), bitmap in self.targets.items():
            if self._bitmap(own, tag, index) != bitmap:
                raise CacheError(f"{own}: {tag}[{index}] changed during injection")
        # Re-read even shared and overlapping ranges after the last edit, without retaining images.
        for (file, offset, size), digest in self.expected.items():
            with self.xiso.open(self.paths[file]) as m:
                m.seek(offset)
                if hashlib.sha256(m.read(size)).digest() != digest:
                    raise CacheError(
                        f"{file}+{offset:#x}: final LOD differs from the encoded image"
                    )
        return signatures


def inject_texture(
    disc: BinaryIO, map_name: str, tag: str, index: int, image: Image.Image
) -> Injection:
    """Replace and validate one native-size bitmap in a writable copy of the disc."""
    injector = DiscInjector(disc)
    bitmap, writes = injector.write(map_name, tag, index, image)
    return Injection(tag, index, bitmap.format, image.size, writes, injector.finish())
