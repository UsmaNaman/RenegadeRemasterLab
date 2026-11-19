"""Halo 2 texture injection into a disc COPY. Synthetic disc built in code only."""

from __future__ import annotations

import json
import sqlite3
import struct
from collections import Counter
from contextlib import closing
from pathlib import Path
from typing import BinaryIO

import numpy as np
import pytest
from fixtures.halo2_builder import bitm_meta, bitmap_entry, make_map, meta_address
from fixtures.xbox_builder import build_partition, make_xbe, write_image
from PIL import Image

from rrl.adapters.xbox_halo2 import inject
from rrl.adapters.xbox_halo2.cachefile import (
    SIGNATURE_OFFSET,
    CacheError,
    compute_signature,
    parse_header,
)
from rrl.adapters.xbox_halo2.inject import DiscInjector, inject_texture
from rrl.cli.main import main
from rrl.containers.xdvdfs import XdvdfsImage, find_partition
from rrl.processors.textures import TextureError, decode, encode_mip_chain

CHAIN = 32 + 8 + 8 + 8  # 8x8 DXT1 down to 1x1


def _map(chain: int = CHAIN, fmt: int = 14) -> bytes:
    def tags(end: int) -> list[tuple[str, str, int | bytes]]:
        out: list[tuple[str, str, int | bytes]] = [("matg", "globals\\globals", 0x40)]
        entry = bitmap_entry(8, 8, fmt, end, chain, mips=3)
        out.append(("bitm", "ui\\logo", bitm_meta(meta_address(out, 1), [entry])))
        return out

    end = len(make_map(tags(0)))
    return make_map(tags(end), tail=bytes(range(256))[:chain])


def _disc(tmp_path: Path, data: bytes) -> Path:
    tree = {"default.xbe": make_xbe(title="Halo 2"), "maps": {"mainmenu.map": data}}
    return write_image(tmp_path / "copy.iso", build_partition(tree))


def _image() -> Image.Image:
    a = np.zeros((8, 8, 3), np.uint8)
    a[:, :4] = (255, 0, 0)
    a[:, 4:] = (0, 0, 255)
    return Image.fromarray(a)


def test_inject_round_trip(tmp_path: Path) -> None:
    iso = _disc(tmp_path, _map())
    before = iso.read_bytes()
    with iso.open("r+b") as disc:
        result = inject_texture(disc, "mainmenu", "ui\\logo", 0, _image())
    assert result.format == "dxt1" and len(result.writes) == 1
    with iso.open("rb") as disc:
        xiso = XdvdfsImage(disc, find_partition(disc) or 0)
        start, size = xiso.locate("maps/mainmenu.map")
        with xiso.open("maps/mainmenu.map") as m:
            assert parse_header(m).signature == compute_signature(m)
            raw_at = size - CHAIN
            m.seek(raw_at)
            got = decode(m.read(32), "dxt1", 8, 8).convert("RGB")
    assert np.asarray(got)[0, 0].tolist() == [255, 0, 0]
    assert np.asarray(got)[0, 7].tolist() == [0, 0, 255]
    after = iso.read_bytes()
    changed = [i for i in range(len(before)) if before[i] != after[i]]
    allowed = set(range(start + raw_at, start + size)) | set(
        range(start + SIGNATURE_OFFSET, start + SIGNATURE_OFFSET + 4)
    )
    assert changed and set(changed) <= allowed  # only the pixels and the signature moved


@pytest.mark.parametrize(
    ("kwargs", "image_size", "message"),
    [
        ({}, (16, 16), "the bitmap is 8x8"),
        ({"fmt": 18, "chain": 256}, (8, 8), "not injectable"),
        ({"chain": 24}, (8, 8), "1 levels are 32 bytes, stored 24"),
    ],
)
def test_inject_refuses(
    tmp_path: Path, kwargs: dict[str, int], image_size: tuple[int, int], message: str
) -> None:
    iso = _disc(tmp_path, _map(**kwargs))
    before = iso.read_bytes()
    with iso.open("r+b") as disc, pytest.raises(CacheError, match=message):
        inject_texture(disc, "mainmenu", "ui\\logo", 0, _image().resize(image_size))
    assert iso.read_bytes() == before  # refused before any write


def test_inject_keeps_padding(tmp_path: Path) -> None:
    iso = _disc(tmp_path, _map(chain=128))  # stored chains are padded to 128 bytes
    with iso.open("r+b") as disc:
        (write,) = inject_texture(disc, "mainmenu", "ui\\logo", 0, _image()).writes
    assert write[2] == CHAIN
    data = iso.read_bytes()
    with iso.open("rb") as disc:
        start, size = XdvdfsImage(disc, find_partition(disc) or 0).locate("maps/mainmenu.map")
    assert data[start + size - 128 + CHAIN : start + size] == bytes(range(256))[CHAIN:128]


def test_inject_short_stored_chain(tmp_path: Path) -> None:
    iso = _disc(tmp_path, _map(chain=CHAIN - 8))  # the 1x1 level is not stored
    with iso.open("r+b") as disc:
        (write,) = inject_texture(disc, "mainmenu", "ui\\logo", 0, _image()).writes
    assert write[2] == CHAIN - 8


def test_dxt3_alpha_round_trip() -> None:
    rng = np.random.default_rng(3)
    rgba = rng.integers(0, 256, (16, 12, 4), dtype=np.uint8)  # noisy alpha, non-square
    chain = encode_mip_chain(Image.fromarray(rgba, "RGBA"), "dxt3", 1)
    got = np.asarray(decode(chain, "dxt3", 12, 16).convert("RGBA"))
    assert int(np.abs(got[..., 3].astype(int) - rgba[..., 3]).max()) <= 8  # one 4-bit step


def test_encode_mip_chain() -> None:
    assert len(encode_mip_chain(_image(), "dxt1", 4)) == CHAIN
    assert len(encode_mip_chain(_image(), "dxt5", 2)) == 64 + 16
    with pytest.raises(TextureError, match="0 mip levels"):
        encode_mip_chain(_image(), "dxt1", 0)
    with pytest.raises(TextureError, match="not supported"):
        encode_mip_chain(_image(), "p8", 1)


@pytest.mark.parametrize("case", ["success", "existing", "source", "wrong_size"])
def test_inject_cli(
    tmp_path: Path, workspace: Path, capsys: pytest.CaptureFixture[str], case: str
) -> None:
    source = _disc(tmp_path, _map())
    before = source.read_bytes()
    source.chmod(0o444)
    png = tmp_path / "edit.png"
    image = _image()
    if case == "wrong_size":
        image = image.resize((16, 16))
    image.save(png)
    out = source if case == "source" else tmp_path / "edited.iso"
    if case == "existing":
        out.write_bytes(b"keep")
    tag = r"ui\logo"
    rc = main(
        [
            "inject",
            str(source),
            "--map",
            "mainmenu",
            "--tag",
            tag,
            "--index",
            "0",
            "--image",
            str(png),
            "--out",
            str(out),
        ]
    )
    captured = capsys.readouterr()
    assert source.read_bytes() == before
    with closing(sqlite3.connect(workspace / "rrl.db")) as conn:
        status, params, path = conn.execute(
            "SELECT o.status, o.params_json, s.path FROM operation o JOIN source s"
            " ON s.id = o.source_id WHERE o.op = 'inject'"
        ).fetchone()
    assert path == str(source)
    assert json.loads(params) == {
        "map": "mainmenu",
        "tag": tag,
        "index": 0,
        "image": str(png),
        "out": str(out),
    }
    if case == "success":
        assert rc == 0 and status == "succeeded"
        assert json.loads(captured.out)["writes"][0][2] == CHAIN
        assert out.read_bytes() != before
    else:
        assert rc == 1 and status == "failed"
        if case == "existing":
            assert "already exists" in captured.err and out.read_bytes() == b"keep"
        elif case == "source":
            assert "equals the source" in captured.err
        else:
            assert "bitmap is 8x8" in captured.err and not out.exists()


def _batch_disc(tmp_path: Path) -> Path:
    # Mainmenu's second bitmap and shared's first refer to the same physical DXT chain.
    shared = _map()
    shared_at = len(shared) - CHAIN

    def tags(at: int) -> list[tuple[str, str, int | bytes]]:
        out: list[tuple[str, str, int | bytes]] = [("matg", "globals", 0x40)]
        entries = [
            bitmap_entry(8, 8, 14, at, CHAIN, mips=3),
            bitmap_entry(8, 8, 14, (2 << 30) | shared_at, CHAIN, mips=3),
            bitmap_entry(8, 8, 22, at + CHAIN, 256),  # v8u8 stays uninjectable
        ]
        out.append(("bitm", r"ui\edit", bitm_meta(meta_address(out, 1), entries)))
        return out

    local = make_map(
        tags(len(make_map(tags(0)))), internal_name="mainmenu", tail=bytes(CHAIN + 256)
    )
    return write_image(
        tmp_path / "source.iso",
        build_partition(
            {
                "default.xbe": make_xbe(title="Halo 2", title_id=0x4D530064),
                "maps": {"mainmenu.map": local, "shared.map": shared},
            }
        ),
    )


def _fake_upscale(png: Path) -> None:
    with Image.open(png) as image:
        enlarged = image.resize((4 * image.width, 4 * image.height), Image.Resampling.NEAREST)
    enlarged.save(png)


@pytest.fixture
def edited_export(tmp_path: Path, workspace: Path) -> Path:
    source, export = _batch_disc(tmp_path), tmp_path / "edits"
    source.chmod(0o444)
    assert main(["extract", str(source), "--export", str(export)]) == 0
    local = export / "mainmenu/ui/edit_00.png"
    Image.new("RGB", (8, 8), "red").save(local)
    _fake_upscale(local)
    for path in ("mainmenu/ui/edit_01.png", "shared/ui/logo_00.png"):
        Image.new("RGB", (8, 8), "lime").save(export / path)
    (export / "extra.png").write_bytes(b"unregistered PNG must be ignored")
    return export


@pytest.mark.parametrize("resize", [False, True])
def test_batch_resize_and_shared_storage(
    edited_export: Path,
    tmp_path: Path,
    workspace: Path,
    resize: bool,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    source, out = tmp_path / "source.iso", tmp_path / "result.iso"
    before = source.read_bytes()
    # One signature calculation to sign and one independent verification per physical map.
    passes: Counter[str] = Counter()

    def counted(m: BinaryIO) -> int:
        passes[parse_header(m).internal_name] += 1
        return compute_signature(m)

    monkeypatch.setattr(inject, "compute_signature", counted)

    with closing(sqlite3.connect(workspace / "rrl.db")) as conn, conn:
        conn.execute(
            "UPDATE artifact SET metadata_json = json_remove(metadata_json, '$.xemu_hashes')"
        )
        # Re-extraction of a path must not double its report count.
        conn.execute(
            """INSERT INTO artifact
                   (source_id, blob_sha256, kind, logical_path, format, metadata_json)
               SELECT source_id, blob_sha256, kind, logical_path, format, metadata_json
               FROM artifact"""
        )
    args = ["inject", "1", "--from", str(edited_export), "--out", str(out)]
    if resize:
        args += ["--resize"]
    capsys.readouterr()
    assert main(args) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["maps"] == {
        "mainmenu": {"injected": 1 + resize, "skipped": 2 - resize},
        "shared": {"injected": 1, "skipped": 0},
    }
    assert "v8u8" in report["skipped"]["mainmenu/ui/edit_02.png"]
    if not resize:
        assert "bitmap is 8x8" in report["skipped"]["mainmenu/ui/edit_00.png"]
    assert passes == ({"testmap": 2, "mainmenu": 2} if resize else {"testmap": 2})
    assert set(report["signatures"]) == (
        {"mainmenu.map", "shared.map"} if resize else {"shared.map"}
    )
    with out.open("rb") as disc:
        xiso = XdvdfsImage(disc, find_partition(disc) or 0)
        for file, (_, new) in report["signatures"].items():
            with xiso.open(f"maps/{file}") as m:
                assert parse_header(m).signature == compute_signature(m) == new
    with closing(sqlite3.connect(workspace / "rrl.db")) as conn:
        status, params, sid = conn.execute(
            "SELECT status, params_json, source_id FROM operation WHERE op = 'inject'"
        ).fetchone()
        assert sid == 1 and status == "succeeded"
        assert json.loads(params) == {
            "from": str(edited_export),
            "out": str(out),
            "only": [],
            "resize": resize,
        }

    reread = tmp_path / "roundtrip"
    assert main(["extract", str(out), "--export", str(reread)]) == 0
    for path in ("mainmenu/ui/edit_01.png", "shared/ui/logo_00.png"):
        with Image.open(reread / path) as green:
            assert green.convert("RGB").getcolors() == [(64, (0, 255, 0))]
    with Image.open(reread / "mainmenu/ui/edit_00.png") as local:
        assert local.convert("RGB").getcolors() == [(64, (255 if resize else 0, 0, 0))]
    assert source.read_bytes() == before


def test_batch_prefixes(
    edited_export: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "result.iso"
    capsys.readouterr()
    assert (
        main(
            [
                "inject",
                "1",
                "--from",
                str(edited_export),
                "--out",
                str(out),
                "--only",
                "mainmenu/ui/edit_01",
                "mainmenu/ui/edit_02",
            ]
        )
        == 0
    )
    report = json.loads(capsys.readouterr().out)
    assert report["maps"] == {"mainmenu": {"injected": 1, "skipped": 1}}
    assert list(report["signatures"]) == ["shared.map"]


@pytest.mark.parametrize("failure", ["signature", "corrupt PNG"])
def test_batch_failure_discards_copy(
    edited_export: Path,
    tmp_path: Path,
    workspace: Path,
    failure: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    source, out = tmp_path / "source.iso", tmp_path / "result.iso"
    before = source.read_bytes()
    if failure == "signature":
        # Corrupt the verification result for the last map after all writes and signatures.
        signatures = iter([0, 0, 0, 1])
        monkeypatch.setattr(inject, "compute_signature", lambda _: next(signatures))
        message = "shared.map: signature does not hold"
    else:
        (edited_export / "shared/ui/logo_00.png").write_bytes(b"broken")
        message = "shared/ui/logo_00.png"
    assert (
        main(["inject", str(source), "--from", str(edited_export), "--out", str(out), "--resize"])
        == 1
    )
    assert message in capsys.readouterr().err
    assert source.read_bytes() == before and not out.exists()
    with closing(sqlite3.connect(workspace / "rrl.db")) as conn:
        assert conn.execute("SELECT status FROM operation WHERE op = 'inject'").fetchone() == (
            "failed",
        )


def test_batch_shared_conflict_is_skipped(
    edited_export: Path,
    tmp_path: Path,
    workspace: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    source, out = tmp_path / "source.iso", tmp_path / "result.iso"
    Image.new("RGB", (8, 8), "blue").save(edited_export / "shared/ui/logo_00.png")
    args = ["inject", str(source), "--from", str(edited_export), "--out", str(out), "--resize"]
    assert main(args) == 0  # the first edit wins; the clashing one is reported, not fatal
    report = json.loads(capsys.readouterr().out)
    assert any("with another edit" in why for why in report["skipped"].values())
    assert out.exists()


def test_batch_matches_only_extracted_paths_of_source(
    edited_export: Path, tmp_path: Path, workspace: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # extra.png exists and is deliberately invalid. These two catalogue entries may not select it.
    with closing(sqlite3.connect(workspace / "rrl.db")) as conn, conn:
        for source_id, kind in ((None, "extracted"), (1, "enhanced")):
            conn.execute(
                """INSERT INTO artifact
                       (source_id, kind, logical_path, blob_sha256, format, metadata_json)
                   SELECT ?, ?, 'extra.png', blob_sha256, format, metadata_json
                   FROM artifact LIMIT 1""",
                (source_id, kind),
            )
    out = tmp_path / "out.iso"
    assert (
        main(["inject", "1", "--from", str(edited_export), "--out", str(out), "--only", "extra"])
        == 1
    )
    assert "no PNGs match" in capsys.readouterr().err and not out.exists()


def test_conflicts_detects_partial_overlap() -> None:
    injector = DiscInjector.__new__(DiscInjector)  # only the bookkeeping is under test
    injector.expected, injector.spans = {("a.map", 100, 50): b"x"}, {"a.map": [(100, 50)]}
    assert not injector._conflicts(("a.map", 100, 50), b"x")  # identical range and bytes
    assert injector._conflicts(("a.map", 100, 50), b"y")  # same range, other bytes
    assert injector._conflicts(("a.map", 120, 8), b"z")  # inside
    assert injector._conflicts(("a.map", 60, 41), b"z")  # runs into it
    assert not injector._conflicts(("a.map", 150, 8), b"z")  # adjacent after
    assert not injector._conflicts(("a.map", 60, 40), b"z")  # adjacent before
    assert not injector._conflicts(("b.map", 100, 50), b"z")  # another file


def test_batch_alpha_and_swizzled_mips(
    tmp_path: Path, workspace: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    def tags(at: int) -> list[tuple[str, str, int | bytes]]:
        out: list[tuple[str, str, int | bytes]] = [("matg", "globals", 0x40)]
        bc1 = bitmap_entry(8, 4, 14, at, 128, mips=3)
        bgra = bytearray(bitmap_entry(8, 4, 11, at + 128, 256, flags=8, mips=3))
        struct.pack_into("<I", bgra, 0x20, at + 384)  # separate LOD1 chain
        struct.pack_into("<I", bgra, 0x38, 128)
        out.append(("bitm", "paint", bitm_meta(meta_address(out, 1), [bc1, bytes(bgra)])))
        return out

    start = len(make_map(tags(0)))
    local = make_map(tags(start), tail=b"\xa5" * 512)
    source = write_image(
        tmp_path / "source.iso",
        build_partition(
            {
                "default.xbe": make_xbe(title="Halo 2", title_id=0x4D530064),
                "maps": {"mainmenu.map": local},
            }
        ),
    )
    before = source.read_bytes()
    export, out = tmp_path / "edit", tmp_path / "result.iso"
    assert main(["extract", str(source), "--export", str(export)]) == 0
    cutout = Image.new("RGBA", (8, 4), (255, 255, 0, 127))
    cutout.paste((255, 0, 0, 128), (1, 1, 7, 4))
    colour = Image.fromarray(np.random.default_rng(10).integers(0, 256, (4, 8, 4), dtype=np.uint8))
    cutout.save(export / "mainmenu/paint_00.png")
    colour.save(export / "mainmenu/paint_01.png")
    capsys.readouterr()
    assert main(["inject", "1", "--from", str(export), "--out", str(out)]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["maps"] == {"mainmenu": {"injected": 2, "skipped": 0}}
    assert source.read_bytes() == before
    with out.open("rb") as disc:
        xiso = XdvdfsImage(disc, find_partition(disc) or 0)
        with xiso.open("maps/mainmenu.map") as m:
            assert (
                parse_header(m).signature
                == compute_signature(m)
                == report["signatures"]["mainmenu.map"][1]
            )
            m.seek(start)
            for w, h in [(8, 4), (4, 2), (2, 1), (1, 1)]:
                got = decode(m.read(16 if w == 8 else 8), "dxt1", w, h)
                level = cutout.resize((w, h), Image.Resampling.LANCZOS)
                assert (
                    got.getchannel("A").tobytes()
                    == level.getchannel("A").point(lambda a: 255 if a >= 128 else 0).tobytes()
                )
            assert m.read(128 - 40) == b"\xa5" * (128 - 40)
            for base, stored in [
                (colour, 256),
                (colour.resize((4, 2), Image.Resampling.LANCZOS), 128),
            ]:
                chain_at = m.tell()
                w, h = base.size
                while True:
                    got = decode(m.read(4 * w * h), "a8r8g8b8", w, h, morton=True)
                    assert got.tobytes() == base.resize((w, h), Image.Resampling.LANCZOS).tobytes()
                    if (w, h) == (1, 1):
                        break
                    w, h = max(1, w // 2), max(1, h // 2)
                pad = chain_at + stored - m.tell()
                assert m.read(pad) == b"\xa5" * pad


def test_linear_injection_fits_pitched_levels(tmp_path: Path) -> None:
    # Only the first two levels fit. The 17-wide surface crosses a 64-byte pitch boundary.
    def tags(at: int) -> list[tuple[str, str, int | bytes]]:
        out: list[tuple[str, str, int | bytes]] = [("matg", "globals", 0x40)]
        entry = bitmap_entry(17, 5, 11, at, 768, flags=16, mips=4)
        out.append(("bitm", "linear", bitm_meta(meta_address(out, 1), [entry])))
        return out

    at = len(make_map(tags(0)))
    iso = _disc(tmp_path, make_map(tags(at), tail=bytes(768)))
    image = Image.fromarray(np.random.default_rng(5).integers(0, 256, (5, 17, 4), dtype=np.uint8))
    with iso.open("r+b") as disc:
        result = inject_texture(disc, "mainmenu", "linear", 0, image)
        assert result.writes == [("mainmenu.map", at, 768)]
        xiso = XdvdfsImage(disc, find_partition(disc) or 0)
        with xiso.open("maps/mainmenu.map") as m:
            m.seek(at)
            for w, h, pitch in [(17, 5, 128), (8, 2, 64)]:
                rows = b"".join(m.read(pitch)[: 4 * w] for _ in range(h))
                got = decode(rows, "a8r8g8b8", w, h)
                assert got.tobytes() == image.resize((w, h), Image.Resampling.LANCZOS).tobytes()


@pytest.mark.parametrize(
    ("fmt", "bpp"), [(0, 1), (1, 1), (2, 1), (3, 2), (6, 2), (8, 2), (9, 2), (10, 4), (11, 4)]
)
def test_inject_validates_representable_channels(tmp_path: Path, fmt: int, bpp: int) -> None:
    # Independent RGBA channels exercise the alpha-only, luminance and packed projections.
    image = Image.fromarray(np.random.default_rng(17).integers(0, 256, (8, 8, 4), dtype=np.uint8))
    iso = _disc(tmp_path, _map(chain=64 * bpp, fmt=fmt))
    with iso.open("r+b") as disc:
        result = inject_texture(disc, "mainmenu", "ui\\logo", 0, image)
        assert result.writes[0][2] == 64 * bpp


@pytest.mark.parametrize(
    ("typ", "fmt", "reason"),
    [
        (0, 17, "2d p8_bump"),
        (0, 18, "2d p8"),
        (0, 22, "2d v8u8"),
        (0, 23, "2d g8b8"),
        (1, 11, "3d a8r8g8b8"),
        (2, 14, "cube dxt1"),
    ],
)
def test_injection_excluded_layouts(tmp_path: Path, typ: int, fmt: int, reason: str) -> None:
    tags: list[tuple[str, str, int | bytes]] = [("matg", "globals", 0x40)]
    entry = bitmap_entry(8, 8, fmt, 0, 128, typ=typ)
    tags.append(("bitm", "excluded", bitm_meta(meta_address(tags, 1), [entry])))
    iso = _disc(tmp_path, make_map(tags))
    before = iso.read_bytes()
    with iso.open("r+b") as disc, pytest.raises(inject.NotInjectable, match=reason):
        inject_texture(disc, "mainmenu", "excluded", 0, _image())
    assert iso.read_bytes() == before
