"""Decode raw texture surfaces into PIL images and encode replacement 2D mip chains.

Knows pixel formats only, never console file formats: the caller hands over the bytes of one
surface plus its format name, size and whether it is Morton-swizzled. Volume (3D) textures go
through decode_volume(), which returns one image per depth slice.

Uncompressed formats use Direct3D naming and are little-endian; channel layouts follow xemu's
NV2A texture tables (hw/xbox/nv2a/pgraph/gl/constants.h in the owner's xemu-vulkan checkout):
- "a8r8g8b8"/"x8r8g8b8": bytes B, G, R, A (A ignored for x8).
- 16-bit formats: one little-endian uint16, fields MSB to LSB as the name reads.
- "a8": white with alpha (xemu swizzle {ONE, ONE, ONE, RED}); returned as LA, L=255.
- "y8": L.  "ay8": the byte is both luminance and alpha (xemu {R, R, R, R}); LA.
- "a8y8": byte 0 luminance, byte 1 alpha (xemu R8G8 with {R, R, R, G}; matches D3D A8L8,
  alpha in the high byte of a little-endian uint16); LA.
- "v8u8": byte 0 = U, byte 1 = V, signed. Shown as a normal-map colour: R = U + 128,
  G = V + 128, B = 255 (Z is not reconstructed), A = 255.
- "g8b8": shown exactly like v8u8 (byte 0 = B, byte 1 = G, read as signed): NV2A takes the sign
  from the sampler, not the format, and the known data (Halo 2 water ripples) is signed.
- "p8": one byte per pixel indexing a caller-supplied 256-entry palette of B, G, R, A bytes
  (D3D A8R8G8B8 little-endian); alpha is kept.
"dxt2"/"dxt4" are the premultiplied-alpha variants of dxt3/dxt5; they decode identically and
the colour is left premultiplied.
"""

from __future__ import annotations

import io
from functools import lru_cache
from typing import Any

import numpy as np
from numpy.typing import NDArray
from PIL import Image

__all__ = [
    "ENCODABLE_FORMATS",
    "FORMATS",
    "TextureError",
    "bytes_needed",
    "decode",
    "decode_p8",
    "decode_volume",
    "deswizzle_box",
    "deswizzle_morton",
    "encode_mip_chain",
    "swizzle_morton",
    "to_png",
]


class TextureError(ValueError):
    """Unsupported format, wrong data length or bad dimensions."""


# format -> (Pillow BCn decoder number, bytes per 4x4 block)
_BLOCK = {"dxt1": (1, 8), "dxt2": (2, 16), "dxt3": (2, 16), "dxt4": (3, 16), "dxt5": (3, 16)}

# 16-bit packed formats: channel -> (shift, bit count), channels in R, G, B, A order
_PACKED16: dict[str, dict[str, tuple[int, int]]] = {
    "r5g6b5": {"r": (11, 5), "g": (5, 6), "b": (0, 5)},
    "a1r5g5b5": {"r": (10, 5), "g": (5, 5), "b": (0, 5), "a": (15, 1)},
    "x1r5g5b5": {"r": (10, 5), "g": (5, 5), "b": (0, 5)},
    "a4r4g4b4": {"r": (8, 4), "g": (4, 4), "b": (0, 4), "a": (12, 4)},
}

_BPP = {
    "a8r8g8b8": 4,
    "x8r8g8b8": 4,
    **dict.fromkeys(_PACKED16, 2),
    "a8": 1,
    "y8": 1,
    "ay8": 1,
    "a8y8": 2,
    "v8u8": 2,
    "g8b8": 2,
    "p8": 1,
}
PALETTE_SIZE = 256 * 4

FORMATS: frozenset[str] = frozenset(_BLOCK) | frozenset(_BPP)


def _check_dims(width: int, height: int) -> None:
    if width < 1 or height < 1:
        raise TextureError(f"bad texture dimensions {width}x{height}")


def _check_format(fmt: str) -> None:
    if fmt not in FORMATS:
        raise TextureError(f"unsupported texture format {fmt!r}")


def bytes_needed(fmt: str, width: int, height: int, *, row_alignment: int = 1) -> int:
    """Size in bytes of one surface (one mip level) of ``fmt`` at ``width`` x ``height``."""
    _check_format(fmt)
    _check_dims(width, height)
    if row_alignment < 1:
        raise TextureError(f"bad row alignment {row_alignment}")
    if fmt in _BLOCK:
        if row_alignment != 1:
            raise TextureError(f"{fmt} blocks do not use a row pitch")
        return ((width + 3) // 4) * ((height + 3) // 4) * _BLOCK[fmt][1]
    row = width * _BPP[fmt]
    return -(-row // row_alignment) * row_alignment * height


def _is_pow2(n: int) -> bool:
    return n > 0 and n & (n - 1) == 0


@lru_cache(maxsize=64)
def _morton_index(width: int, height: int, depth: int = 1) -> NDArray[np.intp]:
    """Swizzled texel offset for every linear texel, z/y/x-major, shape (depth * height * width,).

    Port of generate_swizzle_masks() from xemu hw/xbox/nv2a/pgraph/swizzle.c (LGPL-2.1-or-later,
    (c) espes, Jannik Vogel, Matt Borgerson, the Nouveau Project): bits are handed out x, y, z,
    x, y, z from the LSB while each dimension still needs them; once a dimension runs out, the
    others carry on interleaving, so depth 1 is the plain 2D Morton order.
    """
    coords = [np.arange(n, dtype=np.intp) for n in (width, height, depth)]
    offsets = [np.zeros_like(c) for c in coords]
    out_bit = 0
    bit = 0
    while any((1 << bit) < n for n in (width, height, depth)):
        for n, c, off in zip((width, height, depth), coords, offsets, strict=True):
            if (1 << bit) < n:
                off |= ((c >> bit) & 1) << out_bit
                out_bit += 1
        bit += 1
    off_x, off_y, off_z = offsets
    index = (off_z[:, None, None] | off_y[None, :, None] | off_x[None, None, :]).ravel()
    index.flags.writeable = False  # shared through the cache
    return index


def _morton_pixels(
    data: bytes, width: int, height: int, depth: int, bytes_per_pixel: int
) -> NDArray[np.uint8]:
    _check_dims(width, height)
    if not all(_is_pow2(n) for n in (width, height, depth)):
        raise TextureError(
            f"NV2A swizzle needs power-of-two dimensions, got {width}x{height}x{depth}"
        )
    if bytes_per_pixel < 1:
        raise TextureError(f"bad bytes_per_pixel {bytes_per_pixel}")
    size = width * height * depth * bytes_per_pixel
    if len(data) < size:
        raise TextureError(f"swizzled data is {len(data)} bytes, need {size}")
    return np.frombuffer(data, dtype=np.uint8, count=size).reshape(-1, bytes_per_pixel)


def deswizzle_box(data: bytes, width: int, height: int, depth: int, bytes_per_pixel: int) -> bytes:
    """Reorder an NV2A-swizzled volume into linear slices (row-major rows, slice after slice).

    Port of unswizzle_box() from xemu hw/xbox/nv2a/pgraph/swizzle.c; all three dimensions must
    be powers of two.
    """
    pixels = _morton_pixels(data, width, height, depth, bytes_per_pixel)
    return pixels[_morton_index(width, height, depth)].tobytes()


def deswizzle_morton(data: bytes, width: int, height: int, bytes_per_pixel: int) -> bytes:
    """Reorder a Morton/Z-order (NV2A "swizzled") surface into linear row-major order."""
    return deswizzle_box(data, width, height, 1, bytes_per_pixel)


def swizzle_morton(data: bytes, width: int, height: int, bytes_per_pixel: int) -> bytes:
    """Store each linear texel at the offset deswizzle_morton reads it from."""
    pixels = _morton_pixels(data, width, height, 1, bytes_per_pixel)
    out = np.empty_like(pixels)
    out[_morton_index(width, height, 1)] = pixels
    return out.tobytes()


def _expand(field: NDArray[np.integer[Any]], bits: int) -> NDArray[np.uint8]:
    """Scale an n-bit channel to 8 bits with rounding (so the max value maps to 255)."""
    top = (1 << bits) - 1
    return ((field.astype(np.uint32) * 255 + top // 2) // top).astype(np.uint8)


def decode_p8(indices: bytes, palette: bytes) -> bytes:
    """RGBA bytes for 8-bit palette indices; `palette` is 256 x (B, G, R, A)."""
    if len(palette) != PALETTE_SIZE:
        raise TextureError(f"palette is {len(palette)} bytes, need {PALETTE_SIZE}")
    rgba = np.frombuffer(palette, dtype=np.uint8).reshape(256, 4)[:, [2, 1, 0, 3]]
    return rgba[np.frombuffer(indices, dtype=np.uint8)].tobytes()


def _decode_uncompressed(
    raw: bytes, fmt: str, width: int, height: int, palette: bytes | None
) -> Image.Image:
    size = (width, height)
    if fmt == "p8":
        if palette is None:
            raise TextureError("p8 needs a palette")
        return Image.frombytes("RGBA", size, decode_p8(raw, palette))
    if fmt in ("a8r8g8b8", "x8r8g8b8"):
        bgra = np.frombuffer(raw, dtype=np.uint8).reshape(height, width, 4)
        rgba = bgra[..., [2, 1, 0, 3]].copy()
        if fmt == "x8r8g8b8":
            rgba[..., 3] = 255
        return Image.fromarray(rgba, "RGBA")
    if fmt in _PACKED16:
        px = np.frombuffer(raw, dtype="<u2").reshape(height, width)
        layout = _PACKED16[fmt]
        chans = [
            _expand((px >> layout[c][0]) & ((1 << layout[c][1]) - 1), layout[c][1])
            if c in layout
            else np.full(px.shape, 255, np.uint8)
            for c in "rgba"
        ]
        return Image.fromarray(np.stack(chans, axis=-1), "RGBA")
    if fmt == "y8":
        return Image.frombytes("L", size, raw)
    if fmt == "a8":
        alpha = np.frombuffer(raw, dtype=np.uint8).reshape(height, width)
        return Image.fromarray(np.stack([np.full_like(alpha, 255), alpha], axis=-1), "LA")
    if fmt == "ay8":
        y = np.frombuffer(raw, dtype=np.uint8).reshape(height, width)
        return Image.fromarray(np.stack([y, y], axis=-1), "LA")
    pair = np.frombuffer(raw, dtype=np.uint8).reshape(height, width, 2)
    if fmt == "a8y8":
        return Image.fromarray(pair.copy(), "LA")
    # v8u8 / g8b8: flipping the sign bit maps signed -128..127 onto unsigned 0..255
    uv = pair ^ np.uint8(0x80)
    full = np.full((height, width), 255, np.uint8)
    return Image.fromarray(np.stack([uv[..., 0], uv[..., 1], full, full], axis=-1), "RGBA")


def decode(
    data: bytes,
    fmt: str,
    width: int,
    height: int,
    *,
    morton: bool = False,
    palette: bytes | None = None,
) -> Image.Image:
    """Decode one surface. Extra trailing bytes are ignored; short data raises TextureError.

    `palette` is required for "p8" and ignored otherwise.
    """
    need = bytes_needed(fmt, width, height)
    if len(data) < need:
        raise TextureError(f"{fmt} {width}x{height} needs {need} bytes, got {len(data)}")
    raw = bytes(data[:need])
    if fmt in _BLOCK:
        if morton:
            raise TextureError(f"{fmt} is block-compressed and is never Morton-swizzled")
        # Pillow decodes whole 4x4 blocks: decode the padded size, then crop.
        padded = (((width + 3) // 4) * 4, ((height + 3) // 4) * 4)
        image = Image.frombytes("RGBA", padded, raw, "bcn", _BLOCK[fmt][0])
        return image.crop((0, 0, width, height)) if padded != (width, height) else image
    if morton:
        raw = deswizzle_morton(raw, width, height, _BPP[fmt])
    return _decode_uncompressed(raw, fmt, width, height, palette)


def decode_volume(
    data: bytes,
    fmt: str,
    width: int,
    height: int,
    depth: int,
    *,
    morton: bool = False,
    palette: bytes | None = None,
) -> list[Image.Image]:
    """Decode one level of a volume texture into `depth` slice images (slice 0 first).

    Linear data is `depth` 2D surfaces back to back; swizzled data interleaves x, y and z bits
    across the whole volume (deswizzle_box). Extra trailing bytes are ignored.
    """
    if depth < 1:
        raise TextureError(f"bad volume depth {depth}")
    if fmt in _BLOCK:
        # shortcut: xemu (s3tc_decompress_3d) groups DXT volume blocks 4 slices deep, not as
        # stacked 2D slices; unimplemented until a game is found that uses DXT volumes.
        raise TextureError(f"{fmt} volume textures are not supported")
    slice_size = bytes_needed(fmt, width, height)
    need = slice_size * depth
    if len(data) < need:
        raise TextureError(f"{fmt} {width}x{height}x{depth} needs {need} bytes, got {len(data)}")
    raw = bytes(data[:need])
    if morton:
        raw = deswizzle_box(raw, width, height, depth, _BPP[fmt])
    return [
        _decode_uncompressed(
            raw[z * slice_size : (z + 1) * slice_size], fmt, width, height, palette
        )
        for z in range(depth)
    ]


def to_png(image: Image.Image) -> bytes:
    """Encode an image as PNG bytes."""
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue()


_DDS_HEADER = 128  # Pillow writes plain DXTn DDS files, no DX10 extension header
_DDS_FORMATS = {"dxt1": "DXT1", "dxt3": "DXT3", "dxt5": "DXT5"}
ENCODABLE_FORMATS = frozenset(_DDS_FORMATS) | {
    "a8r8g8b8",
    "x8r8g8b8",
    "r5g6b5",
    "a1r5g5b5",
    "a4r4g4b4",
    "a8",
    "y8",
    "ay8",
    "a8y8",
}


def _encode_bc1_blocks(blocks: NDArray[np.uint8]) -> bytes:
    """Fit one RGB565 segment per block, excluding transparent texels from the colour fit."""
    visible = blocks[:, :, 3] >= 128
    rgb = np.where(visible[:, :, None], blocks[:, :, :3], 0).astype(np.int32)
    four_colour = visible.all(axis=1)
    rows = np.arange(len(blocks))

    # Two farthest-point searches approximate the longest colour axis among visible texels.
    centre = rgb.sum(axis=1) // visible.sum(axis=1).clip(1)[:, None]

    def farthest(origin: NDArray[np.integer[Any]]) -> NDArray[np.int32]:
        distance = np.square(rgb - origin[:, None, :]).sum(axis=2)
        return rgb[rows, np.where(visible, distance, -1).argmax(axis=1)]

    def rgb565(colours: NDArray[np.int32]) -> NDArray[np.int32]:
        r, g, b = ((colours * [31, 63, 31] + 127) // 255).T
        return np.asarray(r << 11 | g << 5 | b, dtype=np.int32)

    first = farthest(centre)
    a, b = rgb565(first), rgb565(farthest(first))
    lo, hi = np.minimum(a, b), np.maximum(a, b)
    # Flat opaque blocks still need distinct endpoints. Keep the original colour in the pair.
    flat = four_colour & (lo == hi)
    lo -= flat & (lo > 0)
    hi += flat & (hi == 0)
    c0, c1 = np.where(four_colour, hi, lo), np.where(four_colour, lo, hi)

    def expand(c: NDArray[np.int32]) -> NDArray[np.int32]:
        r, g, b = c >> 11, (c >> 5) & 63, c & 31
        return np.stack((r << 3 | r >> 2, g << 2 | g >> 4, b << 3 | b >> 2), axis=1)

    p0, p1 = expand(c0), expand(c1)
    p2 = np.where(four_colour[:, None], (2 * p0 + p1) // 3, (p0 + p1) // 2)
    palette = np.stack((p0, p1, p2, (p0 + 2 * p1) // 3), axis=1)
    error = np.square(rgb[:, :, None, :] - palette[:, None, :, :]).sum(axis=3)
    error[~four_colour, :, 3] = 3 * 255**2 + 1  # index 3 is reserved for transparent black
    indices = np.where(visible, error.argmin(axis=2), 3).astype(np.uint32)
    selectors = np.bitwise_or.reduce(indices << np.arange(0, 32, 2, dtype=np.uint32), axis=1)
    return np.column_stack((c0 | c1.astype(np.uint32) << 16, selectors)).astype("<u4").tobytes()


def _encode_dxt1(image: Image.Image) -> bytes:
    rgba = np.asarray(image)
    h, w = rgba.shape[:2]
    padded = np.pad(rgba, ((0, -h % 4), (0, -w % 4), (0, 0)), mode="edge")
    blocks = padded.reshape(padded.shape[0] // 4, 4, padded.shape[1] // 4, 4, 4)
    blocks = blocks.transpose(0, 2, 1, 3, 4).reshape(-1, 16, 4)
    # Bound the palette-distance arrays even for a full-size texture.
    return b"".join(_encode_bc1_blocks(blocks[i : i + 1024]) for i in range(0, len(blocks), 1024))


def _dxt3_from_dxt5(dxt5: bytes, image: Image.Image) -> bytes:
    """Replace each DXT5 block's alpha half with DXT3 alpha: 16 nibbles, row-major, low first."""
    a = np.asarray(image)[..., 3]
    h, w = a.shape
    a = np.pad(a, ((0, -h % 4), (0, -w % 4)), mode="edge")
    blocks = a.reshape(a.shape[0] // 4, 4, a.shape[1] // 4, 4).transpose(0, 2, 1, 3).reshape(-1, 16)
    nibbles = ((blocks.astype(np.uint16) + 8) // 17).astype(np.uint8)
    alpha = nibbles[:, 0::2] | (nibbles[:, 1::2] << 4)
    out = np.frombuffer(dxt5, np.uint8).reshape(-1, 16).copy()
    out[:, :8] = alpha
    return out.tobytes()


def _encode_dxt(image: Image.Image, fmt: str) -> bytes:
    if fmt == "dxt1":
        return _encode_dxt1(image)
    dds = io.BytesIO()
    # Pillow's DXT3 alpha is wrong on real data (errors up to 247): take DXT5's colour
    # blocks, which DXT3 shares, and write the explicit 4-bit alpha ourselves.
    image.save(dds, "DDS", pixel_format="DXT5" if fmt == "dxt3" else _DDS_FORMATS[fmt])
    data = dds.getvalue()[_DDS_HEADER:]
    return _dxt3_from_dxt5(data, image) if fmt == "dxt3" else data


def _encode_uncompressed(image: Image.Image, fmt: str) -> bytes:
    if fmt == "a8r8g8b8":
        return image.tobytes("raw", "BGRA")
    if fmt == "x8r8g8b8":
        return image.convert("RGB").tobytes("raw", "BGRX")
    if fmt in _PACKED16:
        rgba = np.asarray(image, dtype=np.uint16)
        packed = np.zeros(rgba.shape[:2], dtype="<u2")
        for channel, (shift, bits) in _PACKED16[fmt].items():
            value = rgba[..., "rgba".index(channel)]
            packed |= ((value * ((1 << bits) - 1) + 127) // 255) << shift
        return packed.tobytes()
    if fmt == "a8":
        return image.getchannel("A").tobytes()
    if fmt == "a8y8":
        return image.convert("LA").tobytes()
    return image.convert("L").tobytes()  # Y8 and AY8 both store luminance


def encode_mip_chain(
    image: Image.Image, fmt: str, levels: int, *, morton: bool = False, row_alignment: int = 1
) -> bytes:
    """Encode full resolution first, halving each dimension to a minimum of one per level."""
    if fmt not in ENCODABLE_FORMATS:
        raise TextureError(f"encoding {fmt} is not supported")
    if morton and fmt in _BLOCK:
        raise TextureError(f"{fmt} is block-compressed and is never Morton-swizzled")
    if levels < 1:
        raise TextureError(f"{levels} mip levels")
    rgba = image.convert("RGBA")
    out, (w, h) = [], rgba.size
    for _ in range(levels):
        level = rgba if (w, h) == rgba.size else rgba.resize((w, h), Image.Resampling.LANCZOS)
        size = bytes_needed(fmt, w, h, row_alignment=row_alignment)
        if fmt in _DDS_FORMATS:
            data = _encode_dxt(level, fmt)
        else:
            data = _encode_uncompressed(level, fmt)
            if morton:
                data = swizzle_morton(data, w, h, _BPP[fmt])
            if len(data) < size:
                rows = np.frombuffer(data, np.uint8).reshape(h, w * _BPP[fmt])
                data = np.pad(rows, ((0, 0), (0, size // h - rows.shape[1]))).tobytes()
        if len(data) != size:
            raise TextureError(f"encoder gave {len(data)} bytes for a {w}x{h} {fmt} level")
        out.append(data)
        w, h = max(1, w // 2), max(1, h // 2)
    return b"".join(out)
