"""P8 lightmaps (ltmp palettes) and P8 bump (palette from default.xbe). Synthetic data only."""

from __future__ import annotations

import hashlib
import io
from collections.abc import Sequence
from pathlib import Path

import pytest
from fixtures.halo2_builder import (
    bitm_meta,
    bitmap_entry,
    bsp_area,
    make_map,
    meta_address,
    scnr_meta,
    tag_datum,
    xbe_with_section,
)
from fixtures.xbox_builder import build_partition, make_xbe, write_image
from PIL import Image

from rrl.adapters.xbox_halo2 import Halo2Adapter, bump
from rrl.adapters.xbox_halo2.cachefile import CacheError, parse_header, read_tag_index
from rrl.adapters.xbox_halo2.ltmp import read_lightmap_palettes
from rrl.api import Asset, Skipped, Source
from rrl.containers.xdvdfs import XdvdfsImage
from rrl.processors.textures import TextureError, decode_p8, deswizzle_morton

BASE = 0x8F000000  # BSP load address; any value works
P8, P8_BUMP = 18, 17
SWIZZLED = 0x1209
# Synthetic palettes (never the game's): entry i = B, G, R, A
PAL_A = bytes(v for i in range(256) for v in (i, 0, 255 - i, 255))
PAL_B = bytes(v for i in range(256) for v in (0, i, 0, 255 - i))
FAKE_BUMP = bytes(v for i in range(256) for v in (255, i, 128, 255 - i))
INDICES = bytes(range(16))  # one swizzled 4x4 surface


def test_decode_p8() -> None:
    rgba = decode_p8(bytes([0, 1, 255]), PAL_A)
    assert rgba == bytes([255, 0, 0, 255, 254, 0, 1, 255, 0, 0, 255, 255])
    with pytest.raises(TextureError, match="palette is 4 bytes"):
        decode_p8(b"\0", b"\0" * 4)


def _map(
    cluster: Sequence[tuple[int, int]],
    instance: Sequence[tuple[int, int]] = (),
    ltmp_addr: int | None = None,
    bump_pixels: bytes = INDICES,
) -> bytes:
    """matg, a 3-bitmap P8 lightmap bitm, a P8-bump bitm and a scnr with two BSPs (one lightless).

    The tail holds the BSP area, then the lightmap pixels, then the bump pixels.
    """

    def tags(end: int) -> list[tuple[str, str, int | bytes]]:
        area = bsp_area(BASE, tag_datum(1), [PAL_A, PAL_B], cluster, instance, ltmp_addr)
        lm, bm = end + len(area), end + len(area) + 128
        out: list[tuple[str, str, int | bytes]] = [("matg", "globals\\globals", 0x40)]
        out.append(("bitm", "lm\\lightmap", bitm_meta(meta_address(out, 1), [
            bitmap_entry(4, 4, P8, lm, 128, flags=SWIZZLED) for _ in range(3)
        ])))  # fmt: skip
        out.append(("bitm", "bump\\rock", bitm_meta(meta_address(out, 2), [
            bitmap_entry(4, 4, P8_BUMP, bm, 128, flags=0x120D, mips=2),
        ])))  # fmt: skip
        out.append(("scnr", "scenarios\\test", scnr_meta(meta_address(out, 3), [
            (end, len(area), BASE, tag_datum(9)),
            (0x7FFFFFF0, 0x10, BASE, 0xFFFFFFFF),  # no ltmp: never followed
        ])))  # fmt: skip
        return out

    end = len(make_map(tags(0)))
    area = bsp_area(BASE, tag_datum(1), [PAL_A, PAL_B], cluster, instance, ltmp_addr)
    tail = area + INDICES.ljust(128, b"\0") + bump_pixels.ljust(128, b"\0")
    return make_map(tags(end), tail=tail)


def _palettes(data: bytes) -> dict[int, bytes | str]:
    f = io.BytesIO(data)
    h = parse_header(f)
    return read_lightmap_palettes(f, h, read_tag_index(f, h))[tag_datum(1)]


def test_read_lightmap_palettes() -> None:
    pals = _palettes(_map([(0, 1), (-1, 255), (2, 0)], [(0, 1), (1, 5), (2, 1)]))
    assert pals[0] == PAL_B  # cluster and instance agree
    assert pals[1] == "palette index 5 but only 2 section palettes"
    assert pals[2] == "render info gives bitmap 2 conflicting palettes"
    assert -1 not in pals


@pytest.mark.parametrize("ltmp_addr", [BASE - 4, BASE + 0x10000, 0])
def test_ltmp_address_outside_bsp(ltmp_addr: int) -> None:
    # A bad BSP loses only its own lightmaps and reports why; the map read carries on.
    data = _map([(0, 0)], ltmp_addr=ltmp_addr)
    f = io.BytesIO(data)
    h = parse_header(f)
    errors: list[str] = []
    assert read_lightmap_palettes(f, h, read_tag_index(f, h), errors) == {}
    assert len(errors) == 1 and "outside BSP" in errors[0]


def _disc(tmp_path: Path, data: bytes, xbe_va: int | None = bump.BUMP_PALETTE_VA) -> Source:
    xbe = make_xbe(title="Halo 2", title_id=0x4D530064)
    tree = {
        "default.xbe": xbe if xbe_va is None else xbe_with_section(xbe, xbe_va, FAKE_BUMP),
        "maps": {"testmap.map": data},
    }
    iso = write_image(tmp_path / "disc.iso", build_partition(tree))
    return Source(iso, "0" * 64, iso.stat().st_size)


@pytest.fixture
def fake_hash(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bump, "BUMP_PALETTE_SHA256", hashlib.sha256(FAKE_BUMP).hexdigest())


@pytest.mark.usefixtures("fake_hash")
@pytest.mark.parametrize("va", [bump.BUMP_PALETTE_VA, 0x10000])  # fast path, then the scan
def test_find_bump_palette(tmp_path: Path, va: int) -> None:
    source = _disc(tmp_path, b"", va)
    with XdvdfsImage(source.path) as image:
        assert bump.find_bump_palette(image) == FAKE_BUMP


def test_bump_palette_hash_mismatch(tmp_path: Path) -> None:
    source = _disc(tmp_path, b"")  # real hash, synthetic table: must not be accepted
    with XdvdfsImage(source.path) as image, pytest.raises(CacheError, match="unsupported"):
        bump.find_bump_palette(image)


def _expected(palette: bytes) -> list[tuple[int, int, int, int]]:
    rgba = decode_p8(deswizzle_morton(INDICES, 4, 4, 1), palette)
    return [tuple(rgba[i : i + 4]) for i in range(0, len(rgba), 4)]  # type: ignore[misc]


@pytest.mark.usefixtures("fake_hash")
def test_extract_p8(tmp_path: Path) -> None:
    source = _disc(tmp_path, _map([(0, 1), (1, 5)]))
    items = list(Halo2Adapter().extract(source, ["testmap"]))
    assets = {i.logical_path: i for i in items if isinstance(i, Asset)}
    skipped = {i.logical_path: i.reason for i in items if isinstance(i, Skipped)}
    lm = Image.open(io.BytesIO(assets["testmap/lm/lightmap_00.png"].data))
    assert lm.mode == "RGBA" and list(lm.get_flattened_data()) == _expected(PAL_B)
    rock = assets["testmap/bump/rock_00.png"]
    assert list(Image.open(io.BytesIO(rock.data)).get_flattened_data()) == _expected(FAKE_BUMP)
    assert rock.metadata["format"] == "p8_bump" and "xemu_hashes" not in rock.metadata
    assert rock.metadata["palette_sha256"] == hashlib.sha256(FAKE_BUMP).hexdigest()
    assert skipped == {
        "testmap/lm/lightmap_01.png": "palette index 5 but only 2 section palettes",
        "testmap/lm/lightmap_02.png": "no lightmap render info names this bitmap",
    }


def test_extract_without_palettes(tmp_path: Path) -> None:
    # No bump table in the XBE and a broken ltmp pointer: both formats skip, nothing raises.
    source = _disc(tmp_path, _map([(0, 0)], ltmp_addr=0), xbe_va=None)
    items = list(Halo2Adapter().extract(source, ["testmap"]))
    reasons = {i.logical_path: i.reason for i in items if isinstance(i, Skipped)}
    assert len(reasons) == 4 and not any(isinstance(i, Asset) for i in items)
    assert reasons["testmap/bump/rock_00.png"].startswith("no P8 bump palette: unsupported")
    assert "outside BSP" in reasons["testmap/lm/lightmap_00.png"]
