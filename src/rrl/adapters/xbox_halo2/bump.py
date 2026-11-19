"""Find Halo 2's fixed P8-bump palette inside the owner's default.xbe, identified by sha256.

The table itself is game data and is never stored in the repository (ADR-0010).
Layout facts and sources: documentation/platforms/halo2_xbox.md ("P8 bump palette").
"""

from __future__ import annotations

import hashlib
import struct

from rrl.adapters.xbox_halo2.cachefile import CacheError
from rrl.containers.xdvdfs import XdvdfsImage

BUMP_PALETTE_SHA256 = "d077fbea214db2ecde0fcd58967f777a7b7c63f9f5ed1796969b95511c3e38cc"
BUMP_PALETTE_VA = 0x468848  # where the owner's retail XBE keeps it (.data)
PALETTE_SIZE = 0x400
SECTION_HEADER_SIZE = 0x38
MAX_SECTIONS = 256
MAX_XBE_SIZE = 64 * 1024 * 1024


def find_bump_palette(image: XdvdfsImage) -> bytes:
    """The 1024-byte bump palette (256 x B, G, R, A). Raises CacheError if it is not found."""
    with image.open("default.xbe") as f:
        xbe = f.read(MAX_XBE_SIZE + 1)
    if len(xbe) > MAX_XBE_SIZE:
        raise CacheError("default.xbe is absurdly large")
    if (offset := _va_to_offset(xbe, BUMP_PALETTE_VA)) is not None and _is_palette(
        xbe[offset : offset + PALETTE_SIZE]
    ):
        return xbe[offset : offset + PALETTE_SIZE]
    # Other XBE revisions may move the table: try every 4-aligned window (about 3 s for 5 MB).
    for offset in range(0, len(xbe) - PALETTE_SIZE + 1, 4):
        if _is_palette(window := xbe[offset : offset + PALETTE_SIZE]):
            return window
    raise CacheError("unsupported default.xbe revision: P8 bump palette not found")


def _is_palette(data: bytes) -> bool:
    return hashlib.sha256(data).hexdigest() == BUMP_PALETTE_SHA256


def _va_to_offset(xbe: bytes, va: int) -> int | None:
    """File offset of `va` from the section headers, or None if no section holds 1 KiB there."""
    if len(xbe) < 0x124 or xbe[:4] != b"XBEH":
        return None  # not a parseable XBE header: the scan still decides
    (base,) = struct.unpack_from("<I", xbe, 0x104)
    count, headers = struct.unpack_from("<II", xbe, 0x11C)
    start = headers - base
    if not 0 <= count <= MAX_SECTIONS or not (0 <= start <= len(xbe) - count * SECTION_HEADER_SIZE):
        return None  # odd headers: fall back to the scan
    for i in range(count):
        _, sva, _, raw_addr, raw_size = struct.unpack_from(
            "<5I", xbe, start + i * SECTION_HEADER_SIZE
        )
        if sva <= va and va + PALETTE_SIZE <= sva + raw_size:
            offset: int = raw_addr + va - sva
            if offset + PALETTE_SIZE <= len(xbe):
                return offset
    return None
