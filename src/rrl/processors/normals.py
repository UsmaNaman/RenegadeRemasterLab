"""Tangent-space normal maps from edited normals or height fields."""

from __future__ import annotations

import math
import tempfile
from pathlib import Path

import numpy as np
from numpy.typing import NDArray
from PIL import Image

from rrl.processors.textures import to_png


def _encode(x: NDArray[np.float64], y: NDArray[np.float64], z: NDArray[np.float64]) -> Image.Image:
    length = np.hypot(np.hypot(x, y), z)
    # A zero direction has no orientation. Use the outward surface normal.
    z = np.where(length == 0, 1.0, z)
    length = np.where(length == 0, 1.0, length)
    rgb = np.stack((x / length, y / length, z / length), axis=-1)
    return Image.fromarray(np.rint((rgb + 1.0) * 127.5).clip(0, 255).astype(np.uint8))


def fix(image: Image.Image) -> Image.Image:
    if image.mode == "LA":
        x, y = (np.asarray(image, dtype=np.float64) / 127.5 - 1).transpose(2, 0, 1)
        z = np.sqrt(np.maximum(0, 1 - x * x - y * y))
    elif image.mode in ("RGB", "RGBA"):
        x, y, z = (np.asarray(image.convert("RGB"), dtype=np.float64) / 127.5 - 1).transpose(
            2, 0, 1
        )
        z = np.maximum(0, z)
    else:
        raise ValueError("fix requires an RGB, RGBA or two-channel (LA) normal map")
    result = _encode(x, y, z)
    if image.mode == "RGBA":
        result.putalpha(image.getchannel("A"))
    return result


def from_height(image: Image.Image, strength: float, wrap: bool) -> Image.Image:
    if not math.isfinite(strength):
        raise ValueError("--strength must be finite")
    if image.mode == "I;16":
        height = np.asarray(image, dtype=np.float64) / 65535
    else:
        height = np.asarray(image.convert("L"), dtype=np.float64) / 255
    p = np.pad(height, 1, mode="wrap" if wrap else "edge")
    # Sobel divided by eight gives a slope per pixel. Image rows increase
    # downwards, opposite the tangent-space +Y axis.
    dx = (
        (p[:-2, 2:] - p[:-2, :-2]) + 2 * (p[1:-1, 2:] - p[1:-1, :-2]) + (p[2:, 2:] - p[2:, :-2])
    ) / 8
    dy = (
        (p[2:, :-2] - p[:-2, :-2]) + 2 * (p[2:, 1:-1] - p[:-2, 1:-1]) + (p[2:, 2:] - p[:-2, 2:])
    ) / 8
    return _encode(-strength * dx, strength * dy, np.ones_like(height))


def process_tree(
    source: Path, out: Path, *, mode: str, strength: float = 2.0, wrap: bool = False
) -> tuple[int, int]:
    source, out = source.resolve(), out.resolve()
    if source.is_relative_to(out) or out.is_relative_to(source):
        raise ValueError("input and output must be separate, non-overlapping directories")
    if not source.is_dir():
        raise ValueError(f"input directory does not exist: {source}")
    inputs = []
    for path in sorted(source.rglob("*")):
        if path.is_dir() and not path.is_symlink():
            continue
        if path.is_symlink() or not path.is_file() or path.suffix.lower() != ".png":
            raise ValueError(f"expected a regular PNG file: {path}")
        inputs.append(path)
    if not inputs:
        raise ValueError(f"no PNG files in {source}")

    written = skipped = 0
    for path in inputs:
        dest = out / path.relative_to(source)
        if dest.resolve() != dest:
            raise ValueError(f"symlink in output path: {dest}")
        try:
            with Image.open(path, formats=["PNG"]) as image:
                result = fix(image) if mode == "fix" else from_height(image, strength, wrap)
                data = to_png(result)
        except (OSError, SyntaxError, ValueError, Image.DecompressionBombError) as exc:
            raise ValueError(f"cannot process PNG {path}: {exc}") from exc
        if dest.exists() and dest.read_bytes() == data:
            skipped += 1
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=dest.parent, delete=False) as tmp:
            stage = Path(tmp.name)
        try:
            stage.write_bytes(data)
            stage.replace(dest)
        finally:
            stage.unlink(missing_ok=True)
        written += 1
    return written, skipped
