"""Checkpoint a frame-by-frame remaster and publish a finished HEVC movie."""

from __future__ import annotations

import json
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from contextlib import closing
from fractions import Fraction
from pathlib import Path

from rrl.processors.upscale import Texture, upscale_batch


def _ffmpeg(tool: Path, args: list[str], cwd: Path) -> bytes:
    result = subprocess.run(
        [tool, "-nostdin", "-hide_banner", "-loglevel", "error", "-xerror", "-y", *args],
        cwd=cwd,
        capture_output=True,
    )
    if result.returncode:
        raise ValueError(f"ffmpeg failed: {result.stderr.decode(errors='replace').strip()}")
    return result.stdout


def _decode(tool: Path, source: Path, work: Path, deinterlace: bool) -> None:
    # Publish a whole decode together. A killed ffmpeg may leave a valid-looking last PNG.
    with tempfile.TemporaryDirectory(dir=work) as tmp:
        stage = Path(tmp) / "in"
        stage.mkdir()
        filters = ["-vf", "bwdif=mode=send_frame:deint=interlaced"] if deinterlace else []
        _ffmpeg(
            tool,
            [
                "-i",
                str(source),
                "-map",
                "0:v:0",
                *filters,
                "-fps_mode",
                "cfr",
                "-c:v",
                "png",
                "-pix_fmt",
                "rgb24",
                "-f",
                "tee",
                "[f=image2]%09d.png|[f=framecrc]timing.txt",
            ],
            stage,
        )
        # The tee muxer gives both outputs the same encoded packets. Its time base
        # preserves fractional rates such as 30000/1001 without parsing ffmpeg's banner.
        with (stage / "timing.txt").open(encoding="ascii") as timing:
            rate = next(
                (1 / Fraction(s.split()[-1]) for s in timing if s.startswith("#tb 0:")), None
            )
        count = sum(1 for _ in stage.glob("*.png"))
        if not rate or not count:
            raise ValueError("ffmpeg decoded no video frames with a frame rate")
        (stage / "sequence.json").write_text(json.dumps([str(rate), count]), encoding="ascii")
        (stage / "timing.txt").unlink()
        stage.rename(work / "in")


def _encode(tool: Path, source: Path, work: Path, out: Path, rate: str, count: int) -> None:
    with tempfile.TemporaryDirectory(dir=out.parent) as tmp:
        stage = Path(tmp)
        frames = [
            "-framerate",
            rate,
            "-i",
            str(work / "out" / "%09d.png"),
            "-frames:v",
            str(count),
            "-pix_fmt",
            "yuv420p",
            "-tag:v",
            "hvc1",
        ]
        hardware = sys.platform == "darwin"
        if hardware:
            try:
                _ffmpeg(
                    tool, [*frames, "-c:v", "hevc_videotoolbox", "-q:v", "65", "video.mp4"], stage
                )
            except ValueError as exc:
                print(f"{exc}; trying libx265", file=sys.stderr)
                hardware = False
        if not hardware:
            _ffmpeg(tool, [*frames, "-c:v", "libx265", "-crf", "20", "video.mp4"], stage)

        streams = [
            "-progress",
            "pipe:1",
            "-i",
            "video.mp4",
            "-i",
            str(source),
            "-map",
            "0:v:0",
            "-map",
            "1:a?",
            "-c:v",
            "copy",
            "-movflags",
            "+faststart",
        ]
        try:
            progress = _ffmpeg(tool, [*streams, "-c:a", "copy", "movie.mp4"], stage)
        except ValueError as exc:
            # Some FMV audio codecs have no MP4 sample entry. Keep those tracks as AAC.
            print(f"{exc}; trying AAC audio", file=sys.stderr)
            progress = _ffmpeg(tool, [*streams, "-c:a", "aac", "movie.mp4"], stage)
        totals = [line for line in progress.splitlines() if line.startswith(b"frame=")]
        if not totals or int(totals[-1].split(b"=")[1]) != count:
            raise ValueError(f"ffmpeg did not mux all {count} video frames")
        (stage / "movie.mp4").replace(out)


FRAMES_PER_BATCH = 32  # resume granularity vs per-launch overhead


def _upscale_frames(work: Path, backend: Path, model: str, scale: int, count: int) -> None:
    with tempfile.TemporaryDirectory(dir=work) as tmp:
        scratch = Path(tmp)
        names = [f"{i:09d}.png" for i in range(1, count + 1)]
        missing = [n for n in names if not (work / "out" / n).is_file()]
        # One launch per chunk: a model reload (~1 s) outweighs a fast model's frame.
        for start in range(0, len(missing), FRAMES_PER_BATCH):
            chunk = missing[start : start + FRAMES_PER_BATCH]
            textures = [Texture(work / "in" / name, scale) for name in chunk]
            for name, data in zip(
                chunk, upscale_batch(backend, model, textures, scratch), strict=True
            ):
                pending = scratch / name
                pending.write_bytes(data)
                pending.replace(work / "out" / name)


def remaster(
    source: Path,
    out: Path,
    *,
    digest: str,
    backend: Path,
    ffmpeg: Path,
    model: str,
    scale: int,
    deinterlace: bool,
    keep_frames: bool,
) -> int:
    """Return the published frame count. Leave completed frames available after failure."""
    work = out.with_suffix(".frames")
    if source == out or (out.exists() and source.samefile(out)):
        raise ValueError("output equals the source")
    if work == out or work.is_symlink() or source.is_relative_to(work):
        raise ValueError(f"unsafe frame directory: {work}")
    out.parent.mkdir(parents=True, exist_ok=True)
    settings = {
        "sha256": digest,
        "model": model,
        "scale": scale,
        "deinterlace": deinterlace,
        "upscaler": str(backend),
    }
    # Initialize the directory with its ownership marker already present. Never
    # adopt an unrelated directory and delete it after a successful encode.
    if not work.exists():
        with tempfile.TemporaryDirectory(dir=out.parent) as tmp:
            job = Path(tmp) / "frames"
            job.mkdir()
            with closing(sqlite3.connect(job / "job.sqlite")) as lock, lock:
                lock.execute("CREATE TABLE job (settings TEXT NOT NULL)")
                lock.execute("INSERT INTO job VALUES (?)", (json.dumps(settings),))
            (job / "out").mkdir()
            job.rename(work)
    manifest = work / "job.sqlite"
    if not manifest.is_file():
        raise ValueError(f"{work} belongs to a different job; choose another --out")
    # SQLite releases the writer lock even after a hard kill. Two invocations must
    # never change or clean the same PNG sequence while the other is encoding it.
    with (
        closing(sqlite3.connect(manifest.as_uri() + "?mode=rw", uri=True, timeout=0)) as lock,
        lock,
    ):
        lock.execute("BEGIN IMMEDIATE")
        job_row = lock.execute("SELECT settings FROM job").fetchone()
        if not job_row or json.loads(job_row[0]) != settings:
            raise ValueError(f"{work} belongs to a different job; choose another --out")
        inputs = work / "in"
        if not inputs.exists():
            _decode(ffmpeg, source, work, deinterlace)
        rate, count = json.loads((inputs / "sequence.json").read_text(encoding="ascii"))
        _upscale_frames(work, backend, model, scale, count)
        _encode(ffmpeg, source, work, out, rate, count)
        if not keep_frames:
            lock.execute("DELETE FROM job")
    if not keep_frames:
        shutil.rmtree(work)
    return int(count)
