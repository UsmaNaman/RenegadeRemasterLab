"""Texture decoding tests. All pixel data is synthetic and built in this file."""

from __future__ import annotations

import io
import random
import struct
import time

import pytest
from PIL import Image

from rrl.processors.textures import (
    FORMATS,
    TextureError,
    bytes_needed,
    decode,
    decode_volume,
    deswizzle_box,
    deswizzle_morton,
    encode_mip_chain,
    swizzle_morton,
    to_png,
)

# ---------------------------------------------------------------- reference Morton swizzle


def reference_swizzle_offset(x: int, y: int, width: int, height: int) -> int:
    """Interleave the low min(log2 w, log2 h) bits as ...y1x1y0x0, then append the larger
    dimension's remaining bits linearly above them."""
    wb, hb = width.bit_length() - 1, height.bit_length() - 1
    common = min(wb, hb)
    offset = 0
    for i in range(common):
        offset |= ((x >> i) & 1) << (2 * i)
        offset |= ((y >> i) & 1) << (2 * i + 1)
    rest = (x >> common) if wb > hb else (y >> common)
    return offset | (rest << (2 * common))


def reference_swizzle(linear: bytes, width: int, height: int, bpp: int) -> bytes:
    out = bytearray(len(linear))
    for y in range(height):
        for x in range(width):
            src = (y * width + x) * bpp
            dst = reference_swizzle_offset(x, y, width, height) * bpp
            out[dst : dst + bpp] = linear[src : src + bpp]
    return bytes(out)


def xemu_swizzle(linear: bytes, width: int, height: int, bpp: int) -> bytes:
    """Line-for-line port of xemu's generate_swizzle_masks + swizzle_box_internal (depth 1)."""
    mask_x = mask_y = 0
    bit = mask_bit = 1
    while bit < width or bit < height:
        if bit < width:
            mask_x |= mask_bit
            mask_bit <<= 1
        if bit < height:
            mask_y |= mask_bit
            mask_bit <<= 1
        bit <<= 1
    out = bytearray(len(linear))
    off_y = 0
    for y in range(height):
        off_x = 0
        for x in range(width):
            src = (y * width + x) * bpp
            dst = (off_y + off_x) * bpp
            out[dst : dst + bpp] = linear[src : src + bpp]
            off_x = (off_x - mask_x) & mask_x
        off_y = (off_y - mask_y) & mask_y
    return bytes(out)


SIZES = [(8, 8), (64, 64), (16, 4), (4, 16), (256, 64), (1, 8), (8, 1), (1, 1)]


@pytest.mark.parametrize(("w", "h"), SIZES)
@pytest.mark.parametrize("bpp", [1, 2, 4])
def test_swizzle_morton_round_trip(w: int, h: int, bpp: int) -> None:
    linear = random.Random(w * h + bpp).randbytes(w * h * bpp)
    raw = swizzle_morton(linear, w, h, bpp)
    assert raw == reference_swizzle(linear, w, h, bpp)
    assert deswizzle_morton(raw, w, h, bpp) == linear


@pytest.mark.parametrize(("w", "h"), SIZES)
@pytest.mark.parametrize("bpp", [1, 2, 4])
def test_deswizzle_inverts_reference(w: int, h: int, bpp: int) -> None:
    linear = random.Random(w * 1000 + h * 10 + bpp).randbytes(w * h * bpp)
    assert deswizzle_morton(reference_swizzle(linear, w, h, bpp), w, h, bpp) == linear


@pytest.mark.parametrize(("w", "h"), SIZES)
def test_reference_matches_xemu(w: int, h: int) -> None:
    linear = random.Random(w + h).randbytes(w * h * 2)
    assert reference_swizzle(linear, w, h, 2) == xemu_swizzle(linear, w, h, 2)


def test_xemu_comment_example_8x32() -> None:
    # swizzle.c documents 8x32 as mask_x 00010101, mask_y 11101010 ("Z": yyyxyxyx)
    assert reference_swizzle_offset(7, 0, 8, 32) == 0b00010101
    assert reference_swizzle_offset(0, 31, 8, 32) == 0b11101010
    w, h = 8, 32
    linear = bytes(range(w * h))
    back = deswizzle_morton(xemu_swizzle(linear, w, h, 1), w, h, 1)
    assert back == linear


def test_morton_decode() -> None:
    w, h = 4, 2
    linear = bytes(b for i in range(w * h) for b in (i, i * 2, i * 3, 255))  # BGRA
    image = decode(reference_swizzle(linear, w, h, 4), "a8r8g8b8", w, h, morton=True)
    assert image.getpixel((3, 1)) == (21, 14, 7, 255)


# ---------------------------------------------------------------- uncompressed formats


def _pixels(image: Image.Image) -> list[object]:
    return [image.getpixel((x, y)) for y in range(image.height) for x in range(image.width)]


def test_a8r8g8b8_and_x8r8g8b8() -> None:
    data = bytes([0x10, 0x20, 0x30, 0x40, 0xFF, 0x00, 0x80, 0x00])  # two pixels, B G R A
    assert _pixels(decode(data, "a8r8g8b8", 2, 1)) == [(0x30, 0x20, 0x10, 0x40), (0x80, 0, 0xFF, 0)]
    assert _pixels(decode(data, "x8r8g8b8", 2, 1)) == [
        (0x30, 0x20, 0x10, 255),
        (0x80, 0, 0xFF, 255),
    ]


def _u16(*values: int) -> bytes:
    return struct.pack(f"<{len(values)}H", *values)


def test_r5g6b5() -> None:
    data = _u16(0xF800, 0x07E0, 0x001F, 0x0000, (16 << 11) | (32 << 5) | 16)
    assert _pixels(decode(data, "r5g6b5", 5, 1)) == [
        (255, 0, 0, 255),
        (0, 255, 0, 255),
        (0, 0, 255, 255),
        (0, 0, 0, 255),
        (132, 130, 132, 255),  # 16/31, 32/63 rounded
    ]


def test_a1r5g5b5_and_x1r5g5b5() -> None:
    data = _u16(0x8000 | 0x7C00, 0x03E0, 0x001F)
    assert _pixels(decode(data, "a1r5g5b5", 3, 1)) == [
        (255, 0, 0, 255),
        (0, 255, 0, 0),
        (0, 0, 255, 0),
    ]
    assert _pixels(decode(data, "x1r5g5b5", 3, 1)) == [
        (255, 0, 0, 255),
        (0, 255, 0, 255),
        (0, 0, 255, 255),
    ]


def test_a4r4g4b4() -> None:
    assert _pixels(decode(_u16(0x8F05, 0xF0A0), "a4r4g4b4", 2, 1)) == [
        (255, 0, 85, 136),
        (0, 170, 0, 255),
    ]


def test_8bit_formats() -> None:
    data = bytes([0, 128, 255])
    a8 = decode(data, "a8", 3, 1)
    assert a8.mode == "LA" and _pixels(a8) == [(255, 0), (255, 128), (255, 255)]
    y8 = decode(data, "y8", 3, 1)
    assert y8.mode == "L" and _pixels(y8) == [0, 128, 255]
    ay8 = decode(data, "ay8", 3, 1)
    assert ay8.mode == "LA" and _pixels(ay8) == [(0, 0), (128, 128), (255, 255)]


def test_a8y8_low_byte_is_luminance() -> None:
    image = decode(bytes([0x11, 0xEE, 0x22, 0x00]), "a8y8", 2, 1)
    assert image.mode == "LA" and _pixels(image) == [(0x11, 0xEE), (0x22, 0x00)]


@pytest.mark.parametrize(
    "fmt",
    [
        "a8r8g8b8",
        "x8r8g8b8",
        "r5g6b5",
        "a1r5g5b5",
        "a4r4g4b4",
        "a8",
        "y8",
        "ay8",
        "a8y8",
    ],
)
def test_uncompressed_round_trip(fmt: str) -> None:
    raw = random.Random(10).randbytes(bytes_needed(fmt, 32, 16))
    image = decode(raw, fmt, 32, 16)
    encoded = encode_mip_chain(image, fmt, 1)
    assert decode(encoded, fmt, 32, 16).tobytes() == image.tobytes()
    if fmt == "x8r8g8b8":  # the fourth byte is ignored by the decoder
        assert all(encoded[c::4] == raw[c::4] for c in range(3))
    else:
        assert encoded == raw


def test_pack_channels_and_alpha_threshold() -> None:
    image = Image.new("RGBA", (2, 1))
    image.putdata([(255, 128, 0, 127), (8, 0, 255, 128)])
    assert encode_mip_chain(image, "a1r5g5b5", 1) == _u16(0x7E00, 0x841F)
    assert encode_mip_chain(image, "r5g6b5", 1) == _u16(0xFC00, 0x081F)
    assert encode_mip_chain(image, "a4r4g4b4", 1) == _u16(0x7F80, 0x800F)
    assert encode_mip_chain(image, "a8", 1) == bytes((127, 128))
    assert encode_mip_chain(image, "ay8", 1) == image.convert("L").tobytes()


@pytest.mark.parametrize("pitched", [False, True])
def test_uncompressed_storage_per_mip(pitched: bool) -> None:
    image = Image.frombytes("RGBA", (8, 4), random.Random(42).randbytes(128))
    chain = encode_mip_chain(
        image, "a8r8g8b8", 4, morton=not pitched, row_alignment=64 if pitched else 1
    )
    offset = 0
    for w, h in [(8, 4), (4, 2), (2, 1), (1, 1)]:
        level = image if offset == 0 else image.resize((w, h), Image.Resampling.LANCZOS)
        if pitched:
            raw = b"".join(chain[offset + y * 64 : offset + y * 64 + 4 * w] for y in range(h))
            offset += h * 64
        else:
            raw = chain[offset : offset + w * h * 4]
            offset += len(raw)
        assert decode(raw, "a8r8g8b8", w, h, morton=not pitched).tobytes() == level.tobytes()
    assert offset == len(chain)


def test_v8u8_signed_to_colour() -> None:
    # (U, V) = (0, 0), (127, -128), (-1, 1)
    data = bytes([0x00, 0x00, 0x7F, 0x80, 0xFF, 0x01])
    assert _pixels(decode(data, "v8u8", 3, 1)) == [
        (128, 128, 255, 255),
        (255, 0, 255, 255),
        (127, 129, 255, 255),
    ]


def test_g8b8_shown_like_v8u8() -> None:
    data = bytes([0x00, 0x00, 0x7F, 0x80, 0xFF, 0x01])
    assert _pixels(decode(data, "g8b8", 3, 1)) == _pixels(decode(data, "v8u8", 3, 1))


# ---------------------------------------------------------------- volumes


def xemu_swizzle_box(linear: bytes, w: int, h: int, d: int, bpp: int) -> bytes:
    """Line-for-line port of xemu's generate_swizzle_masks + swizzle_box_internal."""
    masks = [0, 0, 0]
    bit = mask_bit = 1
    while bit < w or bit < h or bit < d:
        for i, n in enumerate((w, h, d)):
            if bit < n:
                masks[i] |= mask_bit
                mask_bit <<= 1
        bit <<= 1
    mx, my, mz = masks
    out = bytearray(len(linear))
    off_z = 0
    for z in range(d):
        off_y = 0
        for y in range(h):
            off_x = 0
            for x in range(w):
                src = ((z * h + y) * w + x) * bpp
                dst = (off_z + off_y + off_x) * bpp
                out[dst : dst + bpp] = linear[src : src + bpp]
                off_x = (off_x - mx) & mx
            off_y = (off_y - my) & my
        off_z = (off_z - mz) & mz
    return bytes(out)


BOXES = [(4, 4, 4), (8, 4, 2), (2, 8, 16), (32, 1, 4), (1, 1, 8), (16, 16, 1)]


@pytest.mark.parametrize(("w", "h", "d"), BOXES)
@pytest.mark.parametrize("bpp", [1, 2, 4])
def test_deswizzle_box_inverts_xemu(w: int, h: int, d: int, bpp: int) -> None:
    linear = random.Random(w * h * d + bpp).randbytes(w * h * d * bpp)
    assert deswizzle_box(xemu_swizzle_box(linear, w, h, d, bpp), w, h, d, bpp) == linear


@pytest.mark.parametrize(("w", "h"), SIZES)
def test_deswizzle_box_depth1_is_morton(w: int, h: int) -> None:
    data = random.Random(w + h).randbytes(w * h * 2)
    assert deswizzle_box(data, w, h, 1, 2) == deswizzle_morton(data, w, h, 2)


@pytest.mark.parametrize(("w", "h", "d"), [(4, 4, 3), (3, 4, 4), (4, 4, 0), (0, 4, 4)])
def test_deswizzle_box_bad_dims(w: int, h: int, d: int) -> None:
    with pytest.raises(TextureError):
        deswizzle_box(bytes(256), w, h, d, 1)


def test_deswizzle_box_short_data() -> None:
    with pytest.raises(TextureError, match="need 64"):
        deswizzle_box(bytes(63), 4, 4, 4, 1)


def test_decode_volume_slices() -> None:
    # slice z is filled with luminance z * 10, then swizzled as one volume
    linear = b"".join(bytes([z * 10]) * 16 for z in range(4))
    slices = decode_volume(xemu_swizzle_box(linear, 4, 4, 4, 1), "y8", 4, 4, 4, morton=True)
    assert [img.getcolors() for img in slices] == [[(16, z * 10)] for z in range(4)]
    assert [img.getcolors() for img in decode_volume(linear, "y8", 4, 4, 4)] == [
        [(16, z * 10)] for z in range(4)
    ]


def test_decode_volume_rejects_dxt_and_short_data() -> None:
    with pytest.raises(TextureError, match="dxt1 volume"):
        decode_volume(bytes(64), "dxt1", 4, 4, 4)
    with pytest.raises(TextureError, match="needs 64 bytes"):
        decode_volume(bytes(63), "y8", 4, 4, 4)
    with pytest.raises(TextureError, match="depth"):
        decode_volume(bytes(64), "y8", 4, 4, 0)


# ---------------------------------------------------------------- DXT

RED, BLUE = 0xF800, 0x001F


def _bc1(c0: int, c1: int, indices: list[int]) -> bytes:
    bits = sum(i << (2 * n) for n, i in enumerate(indices))
    return struct.pack("<HHI", c0, c1, bits)


def _close(a: tuple[int, ...], b: tuple[int, ...]) -> bool:
    return all(abs(p - q) <= 1 for p, q in zip(a, b, strict=True))


def test_dxt1_four_colour_block() -> None:
    image = decode(_bc1(RED, BLUE, [0, 1, 2, 3] * 4), "dxt1", 4, 4)
    row = [image.getpixel((x, 0)) for x in range(4)]
    assert row[0] == (255, 0, 0, 255) and row[1] == (0, 0, 255, 255)
    assert _close(row[2], (170, 0, 85, 255)) and _close(row[3], (85, 0, 170, 255))
    assert image.getpixel((3, 3)) == row[3]


def test_dxt1_punch_through_alpha() -> None:
    image = decode(_bc1(BLUE, RED, [0, 3] * 8), "dxt1", 4, 4)  # c0 <= c1: 3-colour mode
    assert image.getpixel((0, 0)) == (0, 0, 255, 255)
    assert image.getpixel((1, 0))[3] == 0


def test_encode_dxt1_alpha_modes_and_edge_blocks() -> None:
    # Cross two block rows with a partial right edge. Hidden RGB must not bias the fit.
    image = Image.new("RGBA", (11, 5), (0, 255, 0, 127))
    for y in range(image.height):
        for x in range(4):
            image.putpixel(
                (x, y),
                [(255, 0, 0, 128), (0, 0, 255, 255), (127, 0, 127, 200), (0, 255, 0, 127)][x],
            )
    image.paste((0, 0, 0, 128), (8, 0, 11, 4))
    image.paste((255, 255, 255, 255), (8, 4, 11, 5))
    chain = encode_mip_chain(image, "dxt1", 4)
    modes = [c0 > c1 for c0, c1, _ in struct.iter_unpack("<HHI", chain[:48])]
    assert modes == [False, False, True] * 2
    offset = 0
    for w, h in [(11, 5), (5, 2), (2, 1), (1, 1)]:
        got = decode(chain[offset:], "dxt1", w, h)
        level = image if offset == 0 else image.resize((w, h), Image.Resampling.LANCZOS)
        for x in range(w):
            for y in range(h):
                pixel, expected = got.getpixel((x, y)), level.getpixel((x, y))
                if expected[3] < 128:
                    assert pixel == (0, 0, 0, 0)
                else:
                    assert pixel[3] == 255
                    if offset == 0:
                        assert _close(pixel[:3], expected[:3])
        offset += bytes_needed("dxt1", w, h)
    assert len(chain) == offset


def test_dxt1_8x8_block_order() -> None:
    blocks = [_bc1(c, c, [0] * 16) for c in (RED, 0x07E0, BLUE, 0xFFFF)]
    image = decode(b"".join(blocks), "dxt1", 8, 8)
    assert image.getpixel((1, 1)) == (255, 0, 0, 255)
    assert image.getpixel((6, 1)) == (0, 255, 0, 255)
    assert image.getpixel((1, 6)) == (0, 0, 255, 255)
    assert image.getpixel((6, 6)) == (255, 255, 255, 255)


@pytest.mark.parametrize(("w", "h"), [(2, 2), (1, 1), (2, 1)])
def test_dxt1_small_mips(w: int, h: int) -> None:
    assert bytes_needed("dxt1", w, h) == 8
    image = decode(_bc1(RED, BLUE, [1] * 16), "dxt1", w, h)
    assert image.size == (w, h) and image.getpixel((w - 1, h - 1)) == (0, 0, 255, 255)


@pytest.mark.parametrize("fmt", ["dxt2", "dxt3"])
def test_dxt3_explicit_alpha(fmt: str) -> None:
    alpha = struct.pack("<Q", sum(n % 16 << (4 * n) for n in range(16)))  # alpha nibble = n
    image = decode(alpha + _bc1(RED, RED, [0] * 16), fmt, 4, 4)
    assert image.getpixel((0, 0)) == (255, 0, 0, 0)
    assert image.getpixel((1, 0)) == (255, 0, 0, 17)
    assert image.getpixel((3, 3)) == (255, 0, 0, 255)


@pytest.mark.parametrize("fmt", ["dxt4", "dxt5"])
def test_dxt5_interpolated_alpha(fmt: str) -> None:
    indices = [0, 1, 2, 7] * 4  # a0, a1, (6*a0+1*a1)/7, (1*a0+6*a1)/7 with a0 > a1
    bits = sum(i << (3 * n) for n, i in enumerate(indices))
    alpha = bytes([210, 0]) + bits.to_bytes(6, "little")
    image = decode(alpha + _bc1(BLUE, BLUE, [0] * 16), fmt, 4, 4)
    assert [image.getpixel((x, 0))[3] for x in range(2)] == [210, 0]
    assert abs(image.getpixel((2, 0))[3] - 180) <= 1 and abs(image.getpixel((3, 0))[3] - 30) <= 1
    assert image.getpixel((0, 0))[:3] == (0, 0, 255)


def test_dxt5_8x8_and_2x2() -> None:
    block = bytes([255, 255]) + bytes(6) + _bc1(RED, RED, [0] * 16)
    assert decode(block * 4, "dxt5", 8, 8).getpixel((7, 7)) == (255, 0, 0, 255)
    assert decode(block, "dxt5", 2, 2).size == (2, 2)


# ---------------------------------------------------------------- sizes, extras, errors


def test_formats_and_sizes() -> None:
    assert {"dxt1", "dxt5", "a8r8g8b8", "v8u8", "a8y8"} <= FORMATS
    assert bytes_needed("dxt1", 8, 8) == 32
    assert bytes_needed("dxt5", 5, 5) == 64
    assert bytes_needed("a8r8g8b8", 4, 2) == 32
    assert bytes_needed("r5g6b5", 4, 2) == 16
    assert bytes_needed("y8", 3, 3) == 9


def test_extra_trailing_bytes_ignored() -> None:
    assert decode(bytes([1, 2, 3, 4]) + b"junk", "a8r8g8b8", 1, 1).getpixel((0, 0)) == (3, 2, 1, 4)


def test_to_png_roundtrip() -> None:
    image = decode(bytes([1, 2, 3, 4]), "a8r8g8b8", 1, 1)
    png = to_png(image)
    assert png.startswith(b"\x89PNG")
    assert Image.open(io.BytesIO(png)).getpixel((0, 0)) == (3, 2, 1, 4)


@pytest.mark.parametrize(
    ("args", "kwargs"),
    [
        ((b"\0" * 3, "a8r8g8b8", 1, 1), {}),  # short
        ((b"\0" * 7, "dxt1", 4, 4), {}),  # short block
        ((b"\0" * 64, "r8g8b8", 4, 4), {}),  # unknown format
        ((b"\0" * 8, "dxt1", 4, 4), {"morton": True}),  # DXT is never swizzled
        ((b"\0" * 48, "a8r8g8b8", 3, 4), {"morton": True}),  # non-power-of-two
        ((b"", "a8", 0, 4), {}),  # bad dimensions
    ],
)
def test_decode_errors(args: tuple[bytes, str, int, int], kwargs: dict[str, bool]) -> None:
    with pytest.raises(TextureError):
        decode(*args, **kwargs)


def test_deswizzle_errors() -> None:
    with pytest.raises(TextureError):
        deswizzle_morton(b"\0" * 12, 3, 1, 4)
    with pytest.raises(TextureError):
        deswizzle_morton(b"\0" * 3, 2, 2, 1)
    with pytest.raises(ValueError):  # TextureError is a ValueError
        bytes_needed("nope", 1, 1)


def test_large_morton_decode_is_fast() -> None:
    data = random.Random(0).randbytes(1024 * 1024 * 4)
    decode(data, "a8r8g8b8", 1024, 1024, morton=True)  # warm the index cache
    start = time.perf_counter()
    image = decode(data, "a8r8g8b8", 1024, 1024, morton=True)
    elapsed = time.perf_counter() - start
    print(f"1024x1024 a8r8g8b8 morton decode: {elapsed * 1000:.1f} ms")
    assert image.size == (1024, 1024)
    assert elapsed < 2.0  # generous; catches an accidental per-pixel Python loop
