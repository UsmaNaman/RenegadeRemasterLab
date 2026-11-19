from __future__ import annotations

import dataclasses
import importlib.util
import io
import sqlite3
import struct
from pathlib import Path
from typing import BinaryIO

import pytest
from fixtures.halo2_builder import bitm_meta, bitmap_entry, make_map, meta_address
from fixtures.xbox_builder import build_partition, make_xbe, write_image
from PIL import Image

from rrl.adapters.registry import check
from rrl.adapters.xbox_halo2 import Halo2Adapter
from rrl.adapters.xbox_halo2.bitmaps import (
    BITM_BLOCK_OFFSET,
    BitmapTag,
    RawLocation,
    read_bitmap_tag,
    read_bitmaps,
    read_raw,
)
from rrl.adapters.xbox_halo2.cachefile import (
    CacheError,
    CacheHeader,
    TagEntry,
    parse_header,
    read_tag_index,
)
from rrl.api import Asset, SelectionError, Skipped, Source
from rrl.cli.main import main
from rrl.emulators.xemu_textures import content_hash

SHARED = 2 << 30
SHARED_PIXELS = bytes(range(16)) * 2  # 8x8 DXT1 = 32 bytes
LOCAL_PIXELS = bytes(range(64))  # 4x4 A8R8G8B8 = 64 bytes


def _tags(local_ptr: int, shared_ptr: int) -> list[tuple[str, str, int | bytes]]:
    tags: list[tuple[str, str, int | bytes]] = [("matg", "globals\\globals", 0x40)]
    tags.append(
        ("bitm", "ui\\local", bitm_meta(meta_address(tags, 1), [
            bitmap_entry(4, 4, 11, local_ptr, len(LOCAL_PIXELS), flags=0x9, mips=2),
        ]))
    )  # fmt: skip
    tags.append(
        ("bitm", "objects\\shared", bitm_meta(meta_address(tags, 2), [
            bitmap_entry(8, 8, 14, SHARED | shared_ptr, len(SHARED_PIXELS), typ=0, flags=0x3),
            bitmap_entry(16, 16, 14, 0xFFFFFFFF, 0, typ=2),
        ]))
    )  # fmt: skip
    tags.append(("scnr", "scenarios\\test\\test", 0x30))
    return tags


def _map(local_ptr: int = 0, shared_ptr: int = 0x100) -> bytes:
    """A map whose local bitmap points at its own tail (when local_ptr is 0)."""
    end = len(make_map(_tags(0, shared_ptr)))
    return make_map(_tags(local_ptr or end, shared_ptr), tail=LOCAL_PIXELS)


def _shared() -> BinaryIO:
    return io.BytesIO(b"\0" * 0x100 + SHARED_PIXELS)


def _open(data: bytes) -> tuple[BinaryIO, list[BitmapTag]]:
    f = io.BytesIO(data)
    h = parse_header(f)
    return f, read_bitmaps(f, h, read_tag_index(f, h))


def test_read_bitmaps_fields() -> None:
    _, tags = _open(_map())
    assert [t.name for t in tags] == ["ui\\local", "objects\\shared"]
    local = tags[0].bitmaps[0]
    assert (local.width, local.height, local.depth, local.format, local.type) == (
        4, 4, 1, "a8r8g8b8", "2d",
    )  # fmt: skip
    assert local.swizzled and not local.linear and local.mips == 2
    assert local.lod0 is not None and local.lod0.file == 0 and local.lod0.file_name is None
    dxt, cube = tags[1].bitmaps
    assert (dxt.format, dxt.swizzled, dxt.index) == ("dxt1", False, 0)
    assert dxt.lod0 == RawLocation(2, 0x100, len(SHARED_PIXELS))
    assert dxt.lod0.file_name == "shared.map"
    assert (cube.type, cube.lod0, cube.index) == ("cube", None, 1)


def test_read_raw_local_and_shared() -> None:
    f, tags = _open(_map())
    opened: list[str] = []
    shared = _shared()

    def opener(name: str) -> BinaryIO:
        opened.append(name)
        return shared

    local, dxt = tags[0].bitmaps[0], tags[1].bitmaps[0]
    assert local.lod0 is not None and dxt.lod0 is not None
    assert read_raw(local.lod0, f, opener) == LOCAL_PIXELS
    assert read_raw(dxt.lod0, f, opener) == SHARED_PIXELS
    assert opened == ["shared.map"]


def test_read_raw_past_eof() -> None:
    f, tags = _open(_map(shared_ptr=0x108))  # 8 bytes too far into the fake shared map
    loc = tags[1].bitmaps[0].lod0
    assert loc is not None
    with pytest.raises(CacheError, match="past end of shared.map"):
        read_raw(loc, f, lambda _: _shared())
    with pytest.raises(CacheError, match="past end of map"):
        read_raw(RawLocation(0, len(_map()) - 4, 8), f, lambda _: _shared())
    with pytest.raises(CacheError, match="absurd raw data size"):
        read_raw(RawLocation(0, 0, 1 << 30), f, lambda _: _shared())


def _corrupt_block(
    count: int, address: int | None = None
) -> tuple[BinaryIO, CacheHeader, list[TagEntry]]:
    data = bytearray(_map())
    f = io.BytesIO(bytes(data))
    h = parse_header(f)
    tags = read_tag_index(f, h)
    off = tags[1].offset
    assert off is not None
    _, old_addr = struct.unpack_from("<iI", data, off + BITM_BLOCK_OFFSET)
    struct.pack_into("<iI", data, off + BITM_BLOCK_OFFSET, count, address or old_addr)
    return io.BytesIO(bytes(data)), h, tags


@pytest.mark.parametrize(
    ("count", "address", "message"),
    [
        (-1, None, "absurd bitmap count"),
        (100_000, None, "absurd bitmap count"),
        (1, 0x10, "outside tag meta"),
        (4000, None, "outside tag meta"),  # block would run past the meta area
    ],
)
def test_malformed_block(count: int, address: int | None, message: str) -> None:
    f, h, tags = _corrupt_block(count, address)
    with pytest.raises(CacheError, match=message):
        read_bitmap_tag(f, h, tags, tags[1])


def test_empty_block_and_missing_meta() -> None:
    f, h, tags = _corrupt_block(0)
    assert read_bitmap_tag(f, h, tags, tags[1]).bitmaps == []
    with pytest.raises(CacheError, match="missing or too small"):
        read_bitmap_tag(f, h, tags, dataclasses.replace(tags[1], offset=None))


# --- extract() end to end on a synthetic disc -------------------------------------------------

A8R8G8B8, DXT1, P8 = 11, 14, 18
SWIZZLED, LINEAR = 0x9, 0x10
RED, GREEN = b"\x00\x00\xff\xff", b"\x00\xff\x00\xff"  # BGRA in memory
PAST_EOF = 9


def _extract_disc(tmp_path: Path) -> Path:
    # A linear 3x2 texture pads rows to 64 bytes; the padding is garbage that must be dropped.
    linear = b"".join((RED * 3).ljust(64, b"\xee") for _ in range(2))
    swizzled = GREEN * 16
    cube = b"".join(bytes([n]) * 8 + b"\0" * 120 for n in range(6))  # 4x4 DXT1 faces, 128 apart
    # 2x2x4 swizzled A8R8G8B8 volume, slice z all (z, 0, 0); depth bits sit above x and y
    volume = b"".join(bytes([0, 0, z, 255]) * 4 for z in range(4)).ljust(128, b"\0")
    raws = [linear, swizzled, cube, volume]
    # (width, height, format, flags, type, raw index / None = shared / PAST_EOF, size)
    entries: list[tuple[int, int, int, int, int, int | None, int]] = [
        (3, 2, A8R8G8B8, LINEAR, 0, 0, len(linear)),
        (4, 4, A8R8G8B8, SWIZZLED, 0, 1, len(swizzled)),
        (4, 4, DXT1, 0x3, 2, 2, len(cube)),
        (8, 8, DXT1, 0x3, 0, None, len(SHARED_PIXELS)),
        (4, 4, P8, 0, 0, 1, 16),  # no ltmp names it
        (2, 2, A8R8G8B8, SWIZZLED, 1, 3, len(volume)),  # 3D texture, depth 4
        (8192, 4, A8R8G8B8, 0, 0, 1, 64),  # absurd dimensions
        (4, 4, A8R8G8B8, SWIZZLED, 0, PAST_EOF, 64),
        (4, 4, A8R8G8B8, LINEAR, 0, 1, 16),  # LOD0 too small for one surface
        (2, 2, A8R8G8B8, LINEAR, 1, 3, len(volume)),  # linear 3D: NV2A never stores one
        (4, 4, DXT1, SWIZZLED, 1, 3, len(volume)),  # DXT volume: layout not supported
        (3, 2, A8R8G8B8, SWIZZLED, 1, 3, len(volume)),  # non-power-of-two volume
    ]

    def tags(pointers: list[int]) -> list[tuple[str, str, int | bytes]]:
        out: list[tuple[str, str, int | bytes]] = [("matg", "globals\\globals", 0x40)]
        bitmaps = [
            bitmap_entry(w, h, fmt, ptr, size, typ=typ, flags=fl, depth=4 if typ == 1 else 1)
            for (w, h, fmt, fl, typ, _, size), ptr in zip(entries, pointers, strict=True)
        ]
        out.append(("bitm", "objects\\..\\thing", bitm_meta(meta_address(out, 1), bitmaps)))
        out.append(("bitm", "broken", 0x10))  # meta too small: skipped, run continues
        return out

    end = len(make_map(tags([0] * len(entries))))
    starts = [end + sum(len(r) for r in raws[:i]) for i in range(len(raws))]
    pointers = [
        SHARED | 0x100 if which is None else end + 0x10000 if which == PAST_EOF else starts[which]
        for *_, which, _ in entries
    ]
    tree = {
        "default.xbe": make_xbe(title="Halo 2", title_id=0x4D530064),
        "maps": {
            "testmap.map": make_map(tags(pointers), tail=b"".join(raws)),
            "shared.map": b"\0" * 0x100 + SHARED_PIXELS,  # raw reads need no valid header
        },
    }
    return write_image(tmp_path / "disc.iso", build_partition(tree))


def test_extract(tmp_path: Path) -> None:
    iso = _extract_disc(tmp_path)
    source = Source(iso, "0" * 64, iso.stat().st_size)
    adapter = Halo2Adapter()
    check(adapter)
    items = list(adapter.extract(source, ["TestMap"]))
    assets = {i.logical_path: i for i in items if isinstance(i, Asset)}
    skipped = {i.logical_path: i.reason for i in items if isinstance(i, Skipped)}
    base = "testmap/objects/thing"  # the ".." segment from the tag name is dropped
    assert sorted(assets) == sorted(
        [f"{base}_00.png", f"{base}_01.png", f"{base}_03.png"]
        + [f"{base}_02_face{n}.png" for n in range(6)]
        + [f"{base}_05_slice{n}.png" for n in range(4)]
    )
    for z in range(4):
        sl = assets[f"{base}_05_slice{z}.png"]
        assert sl.metadata["slice"] == z and sl.metadata["depth"] == 4
        assert Image.open(io.BytesIO(sl.data)).getcolors() == [(4, (z, 0, 0, 255))]
    lin = Image.open(io.BytesIO(assets[f"{base}_00.png"].data))
    assert lin.size == (3, 2) and lin.getcolors() == [(6, (255, 0, 0, 255))]
    swz = Image.open(io.BytesIO(assets[f"{base}_01.png"].data))
    assert swz.getcolors() == [(16, (0, 255, 0, 255))]
    assert assets[f"{base}_03.png"].metadata["raw_file"] == "shared.map"
    assert assets[f"{base}_02_face5.png"].metadata["face"] == 5
    assert assets[f"{base}_00.png"].metadata["format"] == "a8r8g8b8"
    # xemu pack keys: only for swizzled 2D textures in a pack format (ADR-0009)
    if importlib.util.find_spec("xxhash"):
        assert assets[f"{base}_01.png"].metadata["xemu_hashes"] == [
            f"{content_hash(GREEN * 16):016x}"
        ]
    assert "xemu_hashes" not in assets[f"{base}_00.png"].metadata  # linear
    assert "xemu_hashes" not in assets[f"{base}_02_face0.png"].metadata  # cube
    assert skipped[f"{base}_04.png"] == "no lightmap render info names this bitmap"
    assert "absurd dimensions" in skipped[f"{base}_06.png"]
    assert "past end of map" in skipped[f"{base}_07.png"]
    assert "LOD0 holds 16 bytes" in skipped[f"{base}_08.png"]
    assert "linear 3D texture" in skipped[f"{base}_09.png"]
    assert "volume textures are not supported" in skipped[f"{base}_10.png"]
    assert f"{base}_11.png" in skipped  # 3x2 cannot be Morton-swizzled
    assert "too small" in skipped["testmap/broken"]
    # this fake shared.map is not a cache file: reported as Skipped, not raised
    assert [type(i) for i in adapter.extract(source, ["shared"])] == [Skipped]


def test_extract_unknown_map(tmp_path: Path) -> None:
    iso = _extract_disc(tmp_path)
    with pytest.raises(SelectionError, match="no such map"):
        list(Halo2Adapter().extract(Source(iso, "0" * 64, iso.stat().st_size), ["nope"]))


def test_pack_build_cli(
    workspace: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    pytest.importorskip("xxhash")
    iso = _extract_disc(tmp_path)
    export, edited, pack = tmp_path / "export", tmp_path / "edited", tmp_path / "pack"
    assert main(["source", "add", str(iso)]) == 0
    assert main(["extract", "1", "--only", "testmap", "--export", str(export)]) == 0
    png = "testmap/objects/thing_01.png"  # the swizzled 4x4 A8R8G8B8, upscaled 2x
    (edited / png).parent.mkdir(parents=True)
    Image.open(export / png).resize((8, 8)).save(edited / png)
    (edited / "testmap/objects/thing_03.png").write_bytes(b"not a png")
    # the same texture in another map, with one more mip level and no edited PNG of its own
    conn = sqlite3.connect(workspace / "rrl.db")
    with conn:
        conn.execute(
            "INSERT INTO artifact (blob_sha256, source_id, kind, logical_path, format,"
            " metadata_json) SELECT blob_sha256, source_id, kind, 'othermap/thing_01.png',"
            " format, json_set(metadata_json, '$.xemu_hashes[#]', '00000000000000ee')"
            " FROM artifact WHERE logical_path = ?",
            (png,),
        )
    conn.close()
    assert main(["pack", "build", "1", str(edited), str(pack)]) == 0
    out = capsys.readouterr().out
    assert (
        "packed 2 textures" in out
    )  # thing_01 and its copy and "skipped testmap/objects/thing_03.png" in out
    blob = (pack / f"{content_hash(GREEN * 16):016x}.rgba").read_bytes()
    assert blob[4:8] == (8).to_bytes(4, "little")
    assert (pack / "00000000000000ee.rgba").read_bytes() == blob
