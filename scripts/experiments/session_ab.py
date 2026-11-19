"""Experiment: sharpness A/B between two play-session recordings of the same deterministic scene.

Cutscenes play the same in every run but start at different times, so for each time in run B
the closest-looking frame of run A is found (coarse 0.5 s search within +-window, then 0.1 s
refinement) and both frames are scored by edge variance. A zoomed centre crop of each pair is
written next to run B as ab-<t>.png. Reads only RRL's own session videos (via ffmpeg).

Usage: uv run python scripts/experiments/session_ab.py <run_a.mp4> <run_b.mp4> <t1,t2,...>
       [window_s]
"""

from __future__ import annotations

import io
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter

THUMB = (160, 104)  # small enough to ignore encoder noise, big enough to tell shots apart


def grab(video: Path, t: float) -> Image.Image:
    out = subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-ss",
            f"{t:.3f}",
            "-i",
            str(video),
            "-frames:v",
            "1",
            "-f",
            "image2pipe",
            "-vcodec",
            "png",
            "-",
        ],
        capture_output=True,
        check=True,
    ).stdout
    if not out:
        raise ValueError(f"no frame at {t:.1f} s in {video}")
    return Image.open(io.BytesIO(out)).convert("L")


def thumb(im: Image.Image) -> np.ndarray:
    return np.asarray(im.resize(THUMB), float)


def sharpness(im: Image.Image) -> float:
    return float(np.asarray(im.filter(ImageFilter.FIND_EDGES), float).var())


def best_match(
    ref: np.ndarray, frame_at: Callable[[float], np.ndarray], centre: float, window: float
) -> tuple[float, float]:
    """(time, mean abs diff) of the frame in [centre - window, centre + window] closest to ref."""

    def diff(t: float) -> float:
        return float(np.abs(frame_at(t) - ref).mean())

    coarse = [centre - window + k * 0.5 for k in range(int(2 * window / 0.5) + 1)]
    best_t = min((t for t in coarse if t >= 0), key=diff)
    fine = [best_t + k * 0.1 for k in range(-5, 6)]
    best_t = min((t for t in fine if t >= 0), key=diff)
    return best_t, diff(best_t)


def main() -> int:
    if len(sys.argv) not in (4, 5):
        print(__doc__)
        return 2
    run_a, run_b = Path(sys.argv[1]), Path(sys.argv[2])
    times = [float(x) for x in sys.argv[3].split(",")]
    window = float(sys.argv[4]) if len(sys.argv) == 5 else 30.0
    for tb in times:
        fb = grab(run_b, tb)
        ta, d = best_match(thumb(fb), lambda t: thumb(grab(run_a, t)), tb, window)
        fa = grab(run_a, ta)
        print(
            f"B {tb:6.1f}s <-> A {ta:6.1f}s  diff {d:5.1f}  "
            f"sharpness A {sharpness(fa):7.1f}  B {sharpness(fb):7.1f}"
        )
        w, h = fb.size
        box = (w // 4, h // 4, 3 * w // 4, 3 * h // 4)
        pair = Image.new("L", (w, h // 2))
        pair.paste(fa.crop(box), (0, 0))
        pair.paste(fb.crop(box), (w // 2, 0))
        pair.save(run_b.parent / f"ab-{tb:g}.png")
    return 0


if __name__ == "__main__":
    sys.exit(main())
