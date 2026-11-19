"""Generate small PlayStation-shaped ISO trees with arbitrary synthetic payloads."""

import struct
from pathlib import Path

BLOCK = 2048
PAYLOAD = bytes(range(251)) * 137


def both(value: int, width: int) -> bytes:
    return value.to_bytes(width, "little") + value.to_bytes(width, "big")


def record(name: bytes, sector: int, size: int, directory: bool = False) -> bytes:
    buf = bytearray(33 + len(name) + (len(name) % 2 == 0))
    buf[0] = len(buf)
    buf[2:10] = both(sector, 4)
    buf[10:18] = both(size, 4)
    buf[25] = 2 if directory else 0
    buf[28:32] = both(1, 2)
    buf[32] = len(name)
    buf[33 : 33 + len(name)] = name
    return bytes(buf)


def image(ps: int = 2, mode: str = "MODE1/2048") -> bytes:
    sectors = [bytearray(BLOCK) for _ in range(42)]
    vd = sectors[16]
    vd[:7] = b"\1CD001\1"
    vd[80:88] = both(len(sectors), 4)
    vd[120:124] = vd[124:128] = both(1, 2)
    vd[128:132] = both(BLOCK, 2)
    vd[132:140] = both(22, 4)
    vd[140:144] = struct.pack("<I", 18)
    vd[148:152] = struct.pack(">I", 19)
    for i, endian in [(18, "<"), (19, ">")]:
        table = b""
        for s, ident in [(20, b"\0"), (21, b"BIN")]:
            table += struct.pack(endian + "BBIH", len(ident), 0, s, 1) + ident + b"\0"
        sectors[i][: len(table)] = table
    root = record(b"\0", 20, BLOCK, True)
    vd[156:190] = root
    sectors[17][:7] = b"\xffCD001\1"
    serial = "SLUS_123.45" if ps == 2 else "SLES_987.65"
    boot = "BOOT2 = cdrom0:" if ps == 2 else "BOOT = cdrom:"
    cnf = (boot + "\\BIN\\" + serial + ";1\r\nVER = 1.00\r\nVMODE = PAL\r\n").encode()
    sectors[22][: len(cnf)] = cnf
    parent = record(b"\1", 20, BLOCK, True)
    for idx, records in [
        (
            20,
            root + parent + record(b"BIN", 21, BLOCK, True) + record(b"SYSTEM.CNF;1", 22, len(cnf)),
        ),
        (
            21,
            record(b"\0", 21, BLOCK, True)
            + parent
            + record(serial.encode() + b";1", 23, len(PAYLOAD)),
        ),
    ]:
        sectors[idx][: len(records)] = records
    for off in range(0, len(PAYLOAD), BLOCK):
        chunk = PAYLOAD[off : off + BLOCK]
        sectors[23 + off // BLOCK][: len(chunk)] = chunk
    if mode.endswith("/2048"):
        return b"".join(sectors)
    header = b"\0" + b"\xff" * 10 + bytes(4)
    prefix = header + (b"\1" if mode.startswith("MODE1") else b"\2" + bytes(8))
    return b"".join(prefix + s + bytes(2352 - len(prefix) - BLOCK) for s in sectors)


def cue(folder: Path, ps: int = 1) -> Path:
    (folder / "data track.bin").write_bytes(bytes(2352) + image(ps, "MODE2/2352"))
    path = folder / "disc.cue"
    path.write_text(
        'FILE "data track.bin" BINARY\n TRACK 01 MODE2/2352\n'
        " INDEX 00 00:00:00\n INDEX 01 00:00:01\n"
    )
    return path
