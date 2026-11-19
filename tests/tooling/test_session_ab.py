"""Tests for scripts/experiments/session_ab.py on synthetic frames (no video, no game data)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter

SCRIPT = Path(__file__).parents[2] / "scripts" / "experiments" / "session_ab.py"
_spec = importlib.util.spec_from_file_location("session_ab", SCRIPT)
assert _spec and _spec.loader
sab = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sab)


def test_best_match_finds_shifted_frame() -> None:
    # A "video" whose frame content encodes its own time; run A is offset by 7.3 s.
    def frame_at(t: float) -> np.ndarray:
        return np.full(sab.THUMB[::-1], round(t, 1) * 10.0)

    ref = frame_at(47.3)
    t, d = sab.best_match(ref, frame_at, centre=40.0, window=30.0)
    assert abs(t - 47.3) < 1e-6
    assert d < 1e-6


def test_best_match_never_searches_before_zero() -> None:
    seen: list[float] = []

    def frame_at(t: float) -> np.ndarray:
        seen.append(t)
        return np.zeros(sab.THUMB[::-1])

    sab.best_match(np.zeros(sab.THUMB[::-1]), frame_at, centre=2.0, window=30.0)
    assert min(seen) >= 0


def test_sharpness_prefers_sharp_image() -> None:
    rng = np.random.default_rng(0)
    sharp = Image.fromarray((rng.random((64, 64)) * 255).astype(np.uint8))
    blurred = sharp.filter(ImageFilter.GaussianBlur(2))
    assert sab.sharpness(sharp) > sab.sharpness(blurred)
