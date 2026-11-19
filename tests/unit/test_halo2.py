from __future__ import annotations

import io
import json
import struct
from pathlib import Path

import pytest
from fixtures.halo2_builder import make_map, signature
from fixtures.xbox_builder import build_partition, make_xbe, sample_tree, write_image

from rrl.adapters import registry
from rrl.adapters.registry import check
from rrl.adapters.xbox_halo2 import Halo2Adapter
from rrl.adapters.xbox_halo2.cachefile import (
    SIGNATURE_OFFSET,
    CacheError,
    compute_signature,
    parse_header,
    read_tag_index,
    resign,
)
from rrl.adapters.xbox_original import XboxOriginalAdapter
from rrl.api import Source

HALO2_XBE = make_xbe(title="Halo 2", title_id=0x4D530064)


def _source(path: Path) -> Source:
    return Source(path, "0" * 64, path.stat().st_size)


def _patch(data: bytes, offset: int, fmt: str, *values: int) -> bytes:
    out = bytearray(data)
    struct.pack_into(fmt, out, offset, *values)
    return bytes(out)


def _disc(tmp_path: Path, xbe: bytes = HALO2_XBE, maps: dict[str, bytes] | None = None) -> Path:
    if maps is None:
        maps = {
            "testmap.map": make_map(),
            "mainmenu.map": make_map(internal_name="mainmenu", map_type=2),
        }
    tree = {"default.xbe": xbe, "maps": {**maps, "fonts": {"arial-13": b"x"}}}
    return write_image(tmp_path / "disc.iso", build_partition(tree))


def test_parse_header() -> None:
    data = make_map()
    h = parse_header(io.BytesIO(data))
    assert (h.version, h.file_size, h.index_offset) == (8, len(data), 0x800)
    assert (h.internal_name, h.scenario_name) == ("testmap", "scenarios\\test\\testmap")
    assert (h.build, h.map_type_name, h.name_count) == ("00.00.00.00000", "multiplayer", 5)
    assert h.signature == signature(data)


def test_tag_index() -> None:
    f = io.BytesIO(make_map())
    tags = read_tag_index(f, parse_header(f))
    assert [t.group for t in tags] == ["matg", "bitm", "bitm", "scnr", "sbsp"]
    assert tags[0].name == "globals\\globals" and tags[4].name == "scenarios\\test\\bsp"
    assert tags[0].datum == 0xE1740000
    assert tags[4].offset is None  # address 0: BSP data lives elsewhere
    # address translation lands on the exact meta bytes the builder wrote for tag 1
    assert tags[1].offset is not None
    f.seek(tags[1].offset)
    assert f.read(tags[1].size) == bytes((7 + j) & 0xFF for j in range(0x20))


@pytest.mark.parametrize("tail", [b"", b"\x01\x02\x03"])  # short tail is zero-padded
def test_compute_signature(tail: bytes) -> None:
    data = make_map(tail=tail)
    assert compute_signature(io.BytesIO(data)) == signature(data)
    flipped = _patch(data, len(data) - 8, "<I", 0xDEADBEEF)
    assert compute_signature(io.BytesIO(flipped)) != signature(data)


def test_signature_ignores_header() -> None:
    data = make_map()
    assert compute_signature(io.BytesIO(_patch(data, 0x200, "<I", 1234))) == signature(data)


def test_signature_spans_chunks(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("rrl.adapters.xbox_halo2.cachefile.CHUNK", 64)
    data = make_map(tail=b"\x05")
    assert compute_signature(io.BytesIO(data)) == signature(data)


def test_resign_round_trip(tmp_path: Path) -> None:
    copy = tmp_path / "copy.map"
    edited = bytearray(make_map())
    edited[-16] ^= 0xFF  # a "mod"
    copy.write_bytes(edited)
    old, new = resign(copy)
    assert old != new
    data = copy.read_bytes()
    assert struct.unpack_from("<I", data, SIGNATURE_OFFSET)[0] == new == signature(data)
    assert data[:SIGNATURE_OFFSET] == edited[:SIGNATURE_OFFSET]
    assert data[SIGNATURE_OFFSET + 4 :] == edited[SIGNATURE_OFFSET + 4 :]


def test_resign_refuses(tmp_path: Path) -> None:
    with pytest.raises(CacheError, match="regular file"):
        resign(tmp_path)
    target = tmp_path / "real.map"
    target.write_bytes(make_map())
    target.chmod(0o444)
    try:
        with pytest.raises(CacheError, match="writable"):
            resign(target)
    finally:
        target.chmod(0o644)
    disc = tmp_path / "disc.iso"
    disc.write_bytes(build_partition(sample_tree()))
    before = disc.read_bytes()
    with pytest.raises(CacheError, match="magic"):
        resign(disc)
    assert disc.read_bytes() == before


def test_resign_refuses_symlink(tmp_path: Path) -> None:
    target = tmp_path / "real.map"
    target.write_bytes(make_map())
    link = tmp_path / "link.map"
    try:
        link.symlink_to(target)
    except OSError as exc:
        if getattr(exc, "winerror", None) == 1314:
            pytest.skip("Windows account lacks permission to create a symbolic link")
        raise
    with pytest.raises(CacheError, match="regular file"):
        resign(link)


@pytest.mark.parametrize(
    ("data", "message"),
    [
        (b"", "past end"),
        (b"\0" * 0x1000, "magic"),
        (_patch(make_map(), 0x7FC, "<I", 0), "magic"),
        (_patch(make_map(), 4, "<I", 7), "version 7"),
        (_patch(make_map(), 0x10, "<I", 0x10000000), "outside file"),
        (_patch(make_map(), 0x10, "<I", 0x100), "outside file"),
    ],
)
def test_bad_header(data: bytes, message: str) -> None:
    with pytest.raises(CacheError, match=message):
        parse_header(io.BytesIO(data))


@pytest.mark.parametrize(
    ("offset", "fmt", "value", "message"),
    [
        (0x800 + 0x18, "<I", 0x7FFFFFFF, "absurd tag count"),
        (0x800 + 0x18, "<I", 0xFFFF, "past end"),
        (0x800 + 0x08, "<I", 0x10, "outside the tag index"),
        (0x800 + 0x08, "<I", 0xF0000000, "outside the tag index"),
        (0x2CC, "<I", 0x7FFFFFF0, "past end"),  # name index offset
    ],
)
def test_bad_tag_index(offset: int, fmt: str, value: int, message: str) -> None:
    f = io.BytesIO(_patch(make_map(), offset, fmt, value))
    with pytest.raises(CacheError, match=message):
        read_tag_index(f, parse_header(f))


def test_bad_name_offset() -> None:
    data = make_map()
    h = parse_header(io.BytesIO(data))
    f = io.BytesIO(_patch(data, h.name_index_offset, "<I", 0xFFFF))
    with pytest.raises(CacheError, match="outside name table"):
        read_tag_index(f, h)


def test_adapter_passes_registry_checks() -> None:
    check(Halo2Adapter())


def test_identify_precedence(tmp_path: Path) -> None:
    source = _source(_disc(tmp_path))
    matches = registry.identify(source, [XboxOriginalAdapter(), Halo2Adapter()])
    assert [(m.adapter_id, m.confidence) for m in matches] == [
        ("xbox.halo2", 0.95),
        ("xbox.original", 0.9),
    ]
    assert matches[0].platform == "Original Xbox" and matches[0].title_id == "MS-100"


@pytest.mark.parametrize(
    "xbe",
    [make_xbe(), make_xbe(title="Halo 2"), make_xbe(title="Other", title_id=0x4D530064)],
)
def test_identify_rejects_other_games(tmp_path: Path, xbe: bytes) -> None:
    source = _source(_disc(tmp_path, xbe))
    assert Halo2Adapter().identify(source) is None
    with pytest.raises(ValueError, match="not a Halo 2"):
        Halo2Adapter().inspect(source)


def test_inspect(tmp_path: Path) -> None:
    bad = _patch(make_map(), 4, "<I", 9)
    mismatched = _patch(make_map(internal_name="edited"), 0x2D0, "<I", 0)
    source = _source(_disc(tmp_path, maps={"a.map": make_map(), "b.map": mismatched, "c.map": bad}))
    report = Halo2Adapter().inspect(source)
    json.dumps(report)
    assert report["map_count"] == 3 and report["title_id"] == "MS-100"
    a, b, c = report["maps"]
    assert a == {
        "file": "a.map",
        "size": len(make_map()),
        "version": 8,
        "build": "00.00.00.00000",
        "internal_name": "testmap",
        "scenario": "scenarios\\test\\testmap",
        "map_type": "multiplayer",
        "tag_count": 5,
        "top_tag_classes": {"bitm": 2, "matg": 1, "scnr": 1, "sbsp": 1},
        "signature": f"{signature(make_map()):08x}",
        "signature_status": "ok",
    }
    assert b["signature_status"] == "mismatch"
    assert "version 9" in c["error"]


def test_inspect_skips_big_maps_unless_asked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("rrl.adapters.xbox_halo2.VERIFY_LIMIT", 16)
    source = _source(_disc(tmp_path))
    statuses = {m["signature_status"] for m in Halo2Adapter().inspect(source)["maps"]}
    assert statuses == {"unchecked"}
    statuses = {
        m["signature_status"] for m in Halo2Adapter().inspect(source, verify_all=True)["maps"]
    }
    assert statuses == {"ok"}
