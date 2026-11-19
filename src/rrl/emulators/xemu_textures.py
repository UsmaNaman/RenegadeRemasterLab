"""xemu's texture identity: which guest bytes it hashes, and the hash (ADR-0009).

Mirrors pgraph_get_texture_length() in xemu hw/xbox/nv2a/pgraph/texture.c (owner's
xemu-vulkan, branch mac-vulkan) and the XXH3-64 content hash in vk/texture.c create_texture().
Platform-agnostic: callers pass raw guest bytes plus the shape the game programs.
"""

from __future__ import annotations

import io
import struct
from pathlib import Path

from PIL import Image

from rrl.processors.textures import bytes_needed

CUBE_FACE_ALIGNMENT = 128  # NV2A_CUBEMAP_FACE_ALIGNMENT


def texture_length(
    fmt: str,
    width: int,
    height: int,
    levels: int,
    *,
    cube: bool = False,
    depth: int = 1,
    pitch: int | None = None,
) -> int:
    """Bytes xemu hashes for a texture. `pitch` marks a linear texture (one level, rows padded).

    Mip sizes halve with a floor of 1; DXT rounds each level up to whole 4x4 blocks, exactly
    what bytes_needed() does for one surface.
    """
    if levels < 1:
        raise ValueError(f"bad level count {levels}")
    if pitch is not None:
        if cube:
            raise ValueError("linear textures cannot be cube maps")
        return height * pitch
    length = sum(
        bytes_needed(fmt, max(1, width >> lv), max(1, height >> lv)) for lv in range(levels)
    )
    if cube:
        length = -(-length // CUBE_FACE_ALIGNMENT) * CUBE_FACE_ALIGNMENT * 6
    return length * depth


def content_hash(data: bytes) -> int:
    """XXH3-64 of the hashed span, as xemu's fast_hash() computes it (no palette mixed in)."""
    try:
        import xxhash
    except ImportError as exc:  # optional extra
        raise RuntimeError("texture hashing needs: uv sync --extra packs") from exc
    return int(xxhash.xxh3_64_intdigest(data))


# Formats whose pack replacement has been seen to render right (ADR-0009 milestone 2).
PACK_FORMATS = frozenset({"dxt1", "dxt3", "dxt5", "a8r8g8b8"})


def candidate_hashes(raw: bytes, fmt: str, width: int, height: int, levels: int) -> list[int]:
    """xemu hashes of a swizzled 2D mip chain for every level count the game might program.

    Games may program fewer levels than are stored (milestone 1: DXT chains stopping at 4x4),
    so each prefix of the chain that fits in `raw` is a candidate.
    """
    out = []
    for n in range(1, levels + 1):
        length = texture_length(fmt, width, height, n)
        if length > len(raw):
            break
        out.append(content_hash(raw[:length]))
    return out


PACK_SUFFIX = ".rgba"  # kept for every payload format; the magic says which one
DDS_HEADER = 128  # Pillow writes plain DXT1/DXT5 DDS files: no DX10 extension header


def pack_texture(image: Image.Image, guest_width: int, guest_height: int) -> bytes:
    """One texture-pack file, read by the texture-pack xemu build (ADR-0009).

    Layout: a 4-byte magic, little-endian u32 width, height, levels, then every mip level down
    to 1x1 (sizes halve with a floor of 1), tightly packed. Magic b"RRB1" is BC1 (fully opaque
    images), b"RRB3" is BC3; xemu also reads b"RRT1", raw RGBA8. BC keeps the pack and GPU memory
    at a quarter of RGBA or less. xemu only accepts the guest size times a power of two
    (1 included) on both axes, so that is enforced here.
    """
    w, h = image.size
    scale = w // guest_width if guest_width else 0
    if scale < 1 or scale & (scale - 1) or (w, h) != (guest_width * scale, guest_height * scale):
        raise ValueError(f"{w}x{h} is not {guest_width}x{guest_height} times a power of two")
    rgba = image.convert("RGBA")
    opaque = rgba.getchannel("A").getextrema()[0] == 255
    magic, dds_format = (b"RRB1", "DXT1") if opaque else (b"RRB3", "DXT5")
    levels = [rgba]
    while levels[-1].size != (1, 1):
        lw, lh = levels[-1].size
        levels.append(rgba.resize((max(1, lw // 2), max(1, lh // 2)), Image.Resampling.LANCZOS))
    out = [magic, struct.pack("<3I", w, h, len(levels))]
    for lv in levels:
        dds = io.BytesIO()
        lv.save(dds, "DDS", pixel_format=dds_format)
        out.append(dds.getvalue()[DDS_HEADER:])
    return b"".join(out)


def write_pack_texture(
    pack_dir: Path, keys: list[int], image: Image.Image, guest_width: int, guest_height: int
) -> Path:
    """Write <key>.rgba for the first key and hard-link the rest to it (same pixels, other
    level counts). Each file lands via a temp name, so xemu never sees a half-written one."""
    first = pack_dir / f"{keys[0]:016x}{PACK_SUFFIX}"
    tmp = first.with_suffix(".tmp")
    tmp.write_bytes(pack_texture(image, guest_width, guest_height))
    tmp.replace(first)
    for key in keys[1:]:
        tmp.unlink(missing_ok=True)
        tmp.hardlink_to(first)
        tmp.replace(pack_dir / f"{key:016x}{PACK_SUFFIX}")
    return first
