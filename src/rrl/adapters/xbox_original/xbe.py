"""XBE (Original Xbox executable) header and certificate parsing. Identification only."""

from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import BinaryIO

XBE_MAGIC = b"XBEH"
CERT_SIZE = 0xB0  # through the version field; keys after it are never read

REGIONS = {0x1: "NTSC-U", 0x2: "NTSC-J", 0x4: "PAL", 0x80000000: "DEBUG"}
MEDIA = {
    0x1: "HARD_DISK",
    0x2: "DVD_X2",
    0x4: "DVD_CD",
    0x8: "CD",
    0x10: "DVD_5_RO",
    0x20: "DVD_9_RO",
    0x40: "DVD_5_RW",
    0x80: "DVD_9_RW",
    0x100: "DONGLE",
    0x200: "MEDIA_BOARD",
    0x40000000: "NONSECURE_HARD_DISK",
    0x80000000: "NONSECURE_MODE",
}


class XbeError(ValueError):
    """Not a parseable XBE."""


def _flags(value: int, names: dict[int, str]) -> list[str]:
    out = [name for bit, name in names.items() if value & bit]
    rest = value & ~sum(names)
    if rest:
        out.append(f"{rest:#x}")
    return out


@dataclass(frozen=True)
class Certificate:
    title_id: int
    title_name: str
    allowed_media: int
    region: int
    version: int

    @property
    def title_id_str(self) -> str:
        """Publisher letters + game number, e.g. 0x4D530064 -> "MS-100"; hex if not letters."""
        hi, number = self.title_id >> 16, self.title_id & 0xFFFF
        letters = bytes([hi >> 8, hi & 0xFF])
        if letters.isalnum() and letters.isascii():
            return f"{letters.decode()}-{number:03d}"
        return f"{self.title_id:08X}"

    @property
    def title_id_hex(self) -> str:
        return f"{self.title_id:08X}"

    @property
    def region_str(self) -> str:
        return "/".join(_flags(self.region, REGIONS)) or "none"

    @property
    def media_flags(self) -> list[str]:
        return _flags(self.allowed_media, MEDIA)


def read_certificate(f: BinaryIO) -> Certificate:
    """Parse the certificate of an XBE opened for reading. Reads a few hundred bytes."""
    header = f.read(0x11C)
    if len(header) < 0x11C or header[:4] != XBE_MAGIC:
        raise XbeError("missing XBEH magic")
    (base,) = struct.unpack_from("<I", header, 0x104)
    (cert_addr,) = struct.unpack_from("<I", header, 0x118)
    offset = cert_addr - base  # the certificate lives inside the headers, mapped at base
    if offset < 0 or offset > 0x100000:
        raise XbeError(f"certificate address {cert_addr:#x} outside headers (base {base:#x})")
    f.seek(offset)
    cert = f.read(CERT_SIZE)
    if len(cert) < CERT_SIZE:
        raise XbeError("XBE truncated inside certificate")
    (title_id,) = struct.unpack_from("<I", cert, 0x08)
    name = cert[0x0C:0x5C].decode("utf-16-le", errors="replace").split("\0", 1)[0]
    media, region = struct.unpack_from("<II", cert, 0x9C)
    (version,) = struct.unpack_from("<I", cert, 0xAC)
    return Certificate(title_id, name.strip(), media, region, version)
