#!/usr/bin/env python3
"""Refuse to commit console game data, firmware or keys.

Usage: check_no_game_data.py FILE... (exit 1 if any file is blocked).
Stdlib only, so it runs as a pre-commit hook and in CI without installing RRL.
"""

from __future__ import annotations

import sys
from pathlib import Path

MAX_BYTES = 1024 * 1024
HEAD_BYTES = 0x10800  # covers both XDVDFS magic copies in the volume descriptor at 0x10000
ALLOWLIST = Path(__file__).with_name("game_data_allowlist.txt")

BLOCKED_EXTENSIONS = frozenset(
    [
        ".iso",
        ".xiso",
        ".xbe",
        ".xex",
        ".bin",
        ".cue",
        ".img",
        ".chd",
        ".map",
        ".god",
        ".rom",
        ".bios",
        ".key",
        ".keys",
        ".vmc",
        ".mcd",
        ".npy",  # numpy dumps of game tables (a bump palette once slipped in as T.npy)
        ".npz",
    ]
)

XDVDFS_MAGIC = b"MICROSOFT*XBOX*MEDIA"

# (offset, magic, description). Fixed offsets, not substring search, so docs that
# mention a magic string are not flagged.
SIGNATURES: list[tuple[int, bytes, str]] = [
    (0x0, XDVDFS_MAGIC, "Xbox XDVDFS image"),
    (0x10000, XDVDFS_MAGIC, "Xbox XDVDFS image"),
    (0x107EC, XDVDFS_MAGIC, "Xbox XDVDFS image"),
    (0x8001, b"CD001", "ISO 9660 disc image (PS1/PS2 or other)"),
    (0x9311, b"CD001", "raw 2352-byte-sector disc image (Mode 1)"),
    (0x9319, b"CD001", "raw 2352-byte-sector disc image (Mode 2, PS1/PS2)"),
]

# 4-byte magics at offset 0. Only applied to binary-looking files (a NUL in the first
# 512 bytes) so a text file that happens to start with "LIVE" or "head" is not flagged.
FOURCC_AT_0: dict[bytes, str] = {
    b"XBEH": "Xbox executable (XBE)",
    b"XEX2": "Xbox 360 executable (XEX)",
    b"CON ": "Xbox 360 STFS package (CON)",
    b"LIVE": "Xbox 360 STFS package (LIVE)",
    b"PIRS": "Xbox 360 STFS package (PIRS)",
    # Halo cache (.map) header fourcc. Verified against XboxChaos/Assembly
    # Blamite CacheFileLoader.DetermineCacheFileEndianness: "daeh" = little-endian
    # (Halo 1/Halo 2 Xbox, header version 8 for Halo 2), "head" = big-endian (Xbox 360 era).
    b"daeh": "Halo cache file (.map, little-endian)",
    b"head": "Halo cache file (.map, big-endian)",
    b"TIM2": "Sony TIM2 texture",
    b"VAGp": "Sony VAG audio",
}
TIM_MAGIC = b"\x10\x00\x00\x00"  # PS1 TIM; too generic to check outside .tim files


def load_allowlist(path: Path = ALLOWLIST) -> set[str]:
    if not path.is_file():
        return set()
    lines = (line.split("#", 1)[0].strip() for line in path.read_text("utf-8").splitlines())
    return {line for line in lines if line}


def problems(path: Path, allowlist: set[str]) -> list[str]:
    found: list[str] = []
    size = path.stat().st_size
    if size > MAX_BYTES:
        found.append(f"larger than 1 MiB ({size} bytes)")
    suffix = path.suffix.lower()
    if suffix in BLOCKED_EXTENSIONS and path.as_posix() not in allowlist:
        found.append(f"blocked extension {suffix} (see scripts/game_data_allowlist.txt)")
    with path.open("rb") as f:
        head = f.read(HEAD_BYTES)
    for offset, magic, what in SIGNATURES:
        if head[offset : offset + len(magic)] == magic:
            found.append(f"{what} signature at offset {offset:#x}")
    if b"\0" in head[:512] and head[:4] in FOURCC_AT_0:
        found.append(f"{FOURCC_AT_0[head[:4]]} signature at offset 0x0")
    if suffix == ".tim" and head.startswith(TIM_MAGIC):
        found.append("Sony TIM texture signature at offset 0x0")
    return found


def main(argv: list[str]) -> int:
    allowlist = load_allowlist()
    failed = False
    for arg in argv:
        path = Path(arg)
        if not path.is_file():
            continue
        for problem in problems(path, allowlist):
            print(f"{path.as_posix()}: {problem}", file=sys.stderr)
            failed = True
    if failed:
        print(
            "\nRRL never accepts game data, firmware, BIOS dumps or keys. "
            "Remove the file(s) above; see LEGAL.md.",
            file=sys.stderr,
        )
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
