from __future__ import annotations

import struct
from pathlib import Path

import pytest
from PIL import Image

from rrl.emulators.xemu_textures import (
    candidate_hashes,
    content_hash,
    pack_texture,
    texture_length,
    write_pack_texture,
)
from rrl.processors.textures import decode


def test_dxt1_chain_matches_disc_lod0_after_padding() -> None:
    # 512x512 DXT1 with 9 mips below: 174,776 bytes; bitm LOD0 stores align128 = 174,848
    # (halo2_xbox.md, "Bitmaps"); xemu hashes the unpadded chain.
    assert texture_length("dxt1", 512, 512, 10) == 174_776


def test_cube_faces_are_aligned_then_multiplied() -> None:
    # 64x64 DXT1 cube, 6 mips below: 2,744 per face -> 2,816 aligned -> 16,896, which is the
    # retail LOD0 size of mainmenu's ui_reflection.
    assert texture_length("dxt1", 64, 64, 7, cube=True) == 16_896


def test_uncompressed_non_square_floors_at_one() -> None:
    # 8x2 a8r8g8b8, 4 levels: 8x2, 4x1, 2x1, 1x1 pixels
    assert texture_length("a8r8g8b8", 8, 2, 4) == (16 + 4 + 2 + 1) * 4


def test_linear_uses_pitch_times_height() -> None:
    assert texture_length("a8r8g8b8", 123, 121, 1, pitch=512) == 61_952


def test_bad_shapes() -> None:
    with pytest.raises(ValueError):
        texture_length("dxt1", 4, 4, 0)
    with pytest.raises(ValueError):
        texture_length("a8r8g8b8", 4, 4, 1, cube=True, pitch=64)


def test_content_hash_is_xxh3_64() -> None:
    pytest.importorskip("xxhash")
    assert content_hash(b"") == 0x2D06800538D394C2  # XXH3-64 test vector, seed 0


def test_pack_texture_layout_bc3() -> None:
    # 8x4 guest upscaled 2x to 16x8, with alpha: BC3, levels 16x8, 8x4, 4x2, 2x1, 1x1
    blob = pack_texture(Image.new("RGBA", (16, 8), (1, 2, 3, 4)), 8, 4)
    assert blob[:4] == b"RRB3"
    assert struct.unpack("<3I", blob[4:16]) == (16, 8, 5)
    assert len(blob) == 16 + (8 + 2 + 1 + 1 + 1) * 16  # 4x4 blocks, partial ones round up


def test_pack_texture_opaque_is_bc1_and_decodes_back() -> None:
    blob = pack_texture(Image.new("RGB", (8, 8), (255, 0, 0)), 8, 8)
    assert blob[:4] == b"RRB1"
    assert len(blob) == 16 + (4 + 1 + 1 + 1) * 8
    assert decode(blob[16:48], "dxt1", 8, 8).getpixel((3, 3)) == (255, 0, 0, 255)


@pytest.mark.parametrize("size", [(24, 12), (16, 16), (4, 2)])
def test_pack_texture_rejects_non_power_of_two_scale(size: tuple[int, int]) -> None:
    with pytest.raises(ValueError):
        pack_texture(Image.new("RGBA", size), 8, 4)


def test_write_pack_texture_links_every_key(tmp_path: Path) -> None:
    path = write_pack_texture(tmp_path, [0xAB, 0xCD], Image.new("RGB", (4, 4)), 4, 4)
    assert path.name == "00000000000000ab.rgba"
    assert sorted(p.name for p in tmp_path.iterdir()) == [path.name, "00000000000000cd.rgba"]
    assert path.stat().st_nlink == 2
    write_pack_texture(tmp_path, [0xAB, 0xCD], Image.new("RGB", (8, 8)), 4, 4)  # rebuild
    assert (tmp_path / "00000000000000cd.rgba").read_bytes()[4:8] == b"\x08\0\0\0"


def test_candidate_hashes_stop_at_stored_data() -> None:
    pytest.importorskip("xxhash")
    raw = bytes(range(52))  # 8x8 DXT1 levels are 32, 8, 8, 8... bytes: three fit in 52
    keys = candidate_hashes(raw, "dxt1", 8, 8, 6)
    assert keys == [content_hash(raw[:n]) for n in (32, 40, 48)]
    assert candidate_hashes(raw[:31], "dxt1", 8, 8, 4) == []
